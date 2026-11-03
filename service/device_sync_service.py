"""First-stage Device -> Data Plane sync, scoped to the local S1/S6 pilot."""

from urllib.parse import quote, urlsplit

import frappe
import requests

from nextgrp.nextgrp_iot.repository.device_sync_repository import DeviceSyncRepository

STAGING_ORG = "NW-LOCAL-ORG-001"
STAGING_SITE = "NW-LOCAL-SITE-001"
PROFILE_MODELS = {"NW-LOCAL-S1": "TELEMETRY", "NW-LOCAL-S6": "EVENT"}
CONFIG_FIELDS = ("site", "zone", "device_profile", "status", "connectivity_type")
TYPE_MAP = {
    "Float": "number",
    "Int": "number",
    "Integer": "number",
    "Boolean": "boolean",
    "Check": "boolean",
    "Data": "string",
    "String": "string",
}


class DataPlaneSyncError(RuntimeError):
    pass


class DataPlaneVersionConflict(DataPlaneSyncError):
    pass


def eligible(doc):
    return doc.get("site") == STAGING_SITE and doc.get("device_profile") in PROFILE_MODELS


def config_changed(doc, old):
    return any(doc.get(field) != old.get(field) for field in CONFIG_FIELDS)


def positive_version(value, label):
    try:
        version = int(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a positive integer") from None
    if version < 1:
        raise ValueError(f"{label} must be a positive integer")
    return version


class DeviceSyncService:
    def __init__(self, repository=None):
        self.repository = repository if repository is not None else DeviceSyncRepository()

    def validate_device(self, doc):
        if not eligible(doc):
            return

        old = doc.get_doc_before_save()
        try:
            current = positive_version(doc.get("config_version") or 1, "Device config_version")
            if old is None:
                doc.config_version = current
                return

            previous = positive_version(old.get("config_version") or 1, "Previous config_version")
            if config_changed(doc, old):
                doc.config_version = max(previous + 1, current)
            elif current < previous:
                raise ValueError("Device config_version cannot decrease")
            else:
                doc.config_version = current
        except ValueError as exc:
            frappe.throw(str(exc), frappe.ValidationError)

    def device_saved(self, doc):
        if not eligible(doc):
            return

        old = doc.get_doc_before_save()
        if (
            old is None
            or config_changed(doc, old)
            or int(doc.get("config_version") or 1) != int(old.get("config_version") or 1)
        ):
            frappe.enqueue(
                "nextgrp.nextgrp_iot.service.device_sync_service.sync_device_bundle",
                queue="short",
                timeout=300,
                enqueue_after_commit=True,
                device_id=doc.name,
            )

    def build_profile(self, profile_id):
        if profile_id not in PROFILE_MODELS:
            raise ValueError("Unsupported staging profile")
        profile = self.repository.get_document("Device Profile", profile_id)
        points = []
        for row in profile.profile_data_points:
            data_type = TYPE_MAP.get(row.data_type)
            if data_type is None:
                raise ValueError(f"Unsupported data type: {row.data_type}")
            points.append({
                "code": row.code,
                "label": row.label or row.code,
                "data_type": data_type,
                "channel": row.channel or "telemetry",
                "unit": row.unit,
                "required": bool(row.is_required),
            })

        return {
            "profile_code": profile.name,
            "version": positive_version(profile.version, "Profile version"),
            "signal_model": PROFILE_MODELS[profile.name],
            "protocol": "MQTT",
            # Keep the 60s interval agreed for this Data Plane staging pilot.
            "expected_interval_s": 60,
            "heartbeat_interval_s": 60,
            "offline_after_missed": positive_version(profile.offline_after_missed, "offline_after_missed"),
            "data_points": points,
            "event_types": [
                {"code": row.event_code, "label": row.event_name or row.event_code}
                for row in profile.event_types
            ],
        }

    def build_device(self, device):
        if not eligible(device):
            raise ValueError("Device outside staging scope")
        site = self.repository.get_document("Site", device.site)
        if site.organization != STAGING_ORG:
            raise ValueError("Device outside staging Organization")
        if device.zone:
            raise ValueError("Zone mapping not configured for this Device")
        if device.status not in ("Registered", "Active", "Maintenance", "Retired"):
            raise ValueError("Unsupported Device status")
        if device.connectivity_type not in ("WIFI", "LAN", "4G", "5G"):
            raise ValueError("Unsupported connectivity_type")
        return {
            "device_id": device.name,
            "org_code": STAGING_ORG,
            "site_code": STAGING_SITE,
            "zone_code": "DEFAULT",
            "profile_code": device.device_profile,
            "status": device.status.lower(),
            "connectivity_type": device.connectivity_type,
            "config_version": positive_version(device.config_version, "Device config_version"),
            "enabled": device.status != "Retired",
        }

    def build_rule(self, rule, device_id):
        if rule.scope_type != "Device" or rule.scope_ref != device_id:
            raise ValueError("Rule scope mismatch")
        if rule.schedule:
            raise ValueError("Scheduled Rule mapping not implemented")
        if rule.rule_type not in ("THRESHOLD", "STATE_CHANGE"):
            raise ValueError("Unsupported rule type")
        threshold = rule.rule_type == "THRESHOLD"
        return {
            "rule_id": rule.rule_id,
            "version": positive_version(rule.version, "Rule version"),
            "org_code": STAGING_ORG,
            "rule_type": rule.rule_type,
            "scope": {"type": "DEVICE", "ref": device_id},
            "data_point": rule.data_point if threshold else None,
            "operator": rule.operator if threshold else None,
            "threshold": float(rule.threshold) if threshold else None,
            "duration_s": int(rule.duration_s or 0),
            "hysteresis": float(rule.hysteresis or 0),
            "clear_duration_s": int(rule.clear_duration_s or 0),
            "quiet_window_s": int(rule.quiet_window_s or 0),
            "event_type": rule.event_type if not threshold else None,
            "open_limit_s": int(rule.open_limit_s or 0),
            "schedule": None,
            "severity": rule.severity,
            "auto_close": bool(rule.auto_close),
            "active": bool(rule.active),
        }

    def build_operations(self, device):
        device_payload = self.build_device(device)
        profile = self.build_profile(device.device_profile)
        rules = [self.build_rule(rule, device.name) for rule in self.repository.get_device_rules(device.name)]
        operations = [
            ("/api/v1/sync/profiles/" + quote(profile["profile_code"], safe=""), profile),
            ("/api/v1/sync/devices/" + quote(device_payload["device_id"], safe=""), device_payload),
        ]
        operations.extend(
            ("/api/v1/sync/rules/" + quote(rule["rule_id"], safe=""), rule) for rule in rules
        )
        return operations

    def sync_device_bundle(self, device_id):
        device = self.repository.get_document("Device", device_id)
        if not eligible(device):
            return

        base = str(frappe.conf.get("nextwireless_dp_base_url") or "").rstrip("/")
        token = frappe.conf.get("nextwireless_dp_inbound_token")
        parsed = urlsplit(base)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not isinstance(token, str)
            or not token.strip()
            or frappe.conf.get("nextwireless_dp_org_code") != STAGING_ORG
        ):
            raise DataPlaneSyncError("Data Plane site configuration missing or invalid")

        operations = self.build_operations(device)
        with requests.Session() as session:
            session.headers.update({
                "Authorization": f"Bearer {token.strip()}",
                "X-Org-Code": STAGING_ORG,
                "Accept": "application/json",
                "ngrok-skip-browser-warning": "true",
            })
            for path, payload in operations:
                send_put(session, base, path, payload)

        frappe.logger("nextgrp_iot_sync").info("Device bundle sync completed: %s", device_id)


def send_put(session, base, path, payload):
    response = session.put(base + path, json=payload, timeout=(5, 15), allow_redirects=False)
    # Do not log remote response bodies or credentials on errors.
    if response.status_code == 409:
        raise DataPlaneVersionConflict(f"Data Plane version conflict: {path} (HTTP 409)")
    if not 200 <= response.status_code < 300:
        raise DataPlaneSyncError(f"Data Plane HTTP {response.status_code}: {path}")
    try:
        body = response.json()
    except ValueError:
        raise DataPlaneSyncError(f"Invalid Data Plane JSON ACK: {path}") from None
    if not isinstance(body, dict) or body.get("status") not in ("applied", "duplicate"):
        raise DataPlaneSyncError(f"Unexpected Data Plane ACK: {path}")

    expected_version = payload.get("version", payload.get("config_version"))
    if body.get("applied_version") is not None:
        try:
            applied_version = positive_version(body["applied_version"], "applied_version")
        except ValueError:
            raise DataPlaneSyncError(f"Invalid Data Plane applied_version: {path}") from None
        if applied_version != expected_version:
            raise DataPlaneSyncError(f"Unexpected Data Plane applied_version: {path}")

    frappe.logger("nextgrp_iot_sync").info(
        "Sync %s => %s version=%s", path, body["status"], expected_version
    )
    return body


def sync_device_bundle(device_id):
    """Importable worker entry point, intentionally not whitelisted."""
    return DeviceSyncService().sync_device_bundle(device_id)
