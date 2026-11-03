"""Build and send mapped Device/Profile/Rule synchronization payloads."""

from urllib.parse import quote

import frappe
import requests

from nextgrp.nextgrp_iot.repository.device_sync_repository import (
    DeviceSyncRepository,
)

from .integration_mapping import (
    get_device_read_mapping,
    get_rule_remote_id,
)
from .sync_transport import (
    DataPlaneSyncError,
    DataPlaneVersionConflict,
    load_sync_connection,
    positive_version,
    send_put as transport_send_put,
)


STAGING_ORG = "NW-LOCAL-ORG-001"
STAGING_SITE = "NW-LOCAL-SITE-001"

PROFILE_MODELS = {
    "NW-LOCAL-S1": "TELEMETRY",
    "NW-LOCAL-S6": "EVENT",
}

CONFIG_FIELDS = (
    "site",
    "zone",
    "device_profile",
    "status",
    "connectivity_type",
)

TYPE_MAP = {
    "Float": "number",
    "Int": "number",
    "Integer": "number",
    "Boolean": "boolean",
    "Check": "boolean",
    "Data": "string",
    "String": "string",
}


def eligible(doc):
    """Preserve the existing pilot boundary."""

    return (
        doc.get("site") == STAGING_SITE
        and doc.get("device_profile") in PROFILE_MODELS
    )


def config_changed(doc, old):
    return any(
        doc.get(field) != old.get(field)
        for field in CONFIG_FIELDS
    )


class DeviceSyncService:
    def __init__(self, repository=None):
        self.repository = (
            repository
            if repository is not None
            else DeviceSyncRepository()
        )

    def get_device_mapping(self, device):
        if not eligible(device):
            raise ValueError("Device outside staging scope")

        site = self.repository.get_document(
            "Site",
            device.site,
        )

        if site.organization != STAGING_ORG:
            raise ValueError(
                "Device outside staging Organization"
            )

        return get_device_read_mapping(
            device,
            site.organization,
        )

    def validate_device(self, doc):
        """Preserve existing Device version behavior."""

        if not eligible(doc):
            return

        old = doc.get_doc_before_save()

        try:
            current = positive_version(
                doc.get("config_version") or 1,
                "Device config_version",
            )

            if old is None:
                doc.config_version = current
                return

            previous = positive_version(
                old.get("config_version") or 1,
                "Previous config_version",
            )

            if config_changed(doc, old):
                doc.config_version = max(
                    previous + 1,
                    current,
                )
            elif current < previous:
                raise ValueError(
                    "Device config_version cannot decrease"
                )
            else:
                doc.config_version = current

        except ValueError as exc:
            frappe.throw(
                str(exc),
                frappe.ValidationError,
            )

    def device_saved(self, doc):
        """Schedule the existing worker after the save commits."""

        if not eligible(doc):
            return

        old = doc.get_doc_before_save()

        if (
            old is None
            or config_changed(doc, old)
            or int(doc.get("config_version") or 1)
            != int(old.get("config_version") or 1)
        ):
            frappe.enqueue(
                "nextgrp.nextgrp_iot.service."
                "device_sync_service.sync_device_bundle",
                queue="short",
                timeout=300,
                enqueue_after_commit=True,
                device_id=doc.name,
            )

    def build_profile(self, profile_id):
        if profile_id not in PROFILE_MODELS:
            raise ValueError("Unsupported staging profile")

        profile = self.repository.get_document(
            "Device Profile",
            profile_id,
        )

        points = []

        for row in profile.profile_data_points:
            data_type = TYPE_MAP.get(row.data_type)

            if data_type is None:
                raise ValueError(
                    f"Unsupported data type: {row.data_type}"
                )

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
            "version": positive_version(
                profile.version,
                "Profile version",
            ),
            "signal_model": PROFILE_MODELS[profile.name],
            "protocol": "MQTT",
            "expected_interval_s": 60,
            "heartbeat_interval_s": 60,
            "offline_after_missed": positive_version(
                profile.offline_after_missed,
                "offline_after_missed",
            ),
            "data_points": points,
            "event_types": [
                {
                    "code": row.event_code,
                    "label": row.event_name or row.event_code,
                }
                for row in profile.event_types
            ],
        }

    def build_device(self, device):
        mapping = self.get_device_mapping(device)

        if device.zone:
            raise ValueError(
                "Zone mapping not configured for this Device"
            )

        if device.status not in (
            "Registered",
            "Active",
            "Maintenance",
            "Retired",
        ):
            raise ValueError("Unsupported Device status")

        if device.connectivity_type not in (
            "WIFI",
            "LAN",
            "4G",
            "5G",
        ):
            raise ValueError("Unsupported connectivity_type")

        return {
            "device_id": mapping.remote_device_id,
            "org_code": mapping.remote_org_code,
            "site_code": mapping.remote_site_code,
            "zone_code": "DEFAULT",
            "profile_code": device.device_profile,
            "status": device.status.lower(),
            "connectivity_type": device.connectivity_type,
            "config_version": positive_version(
                device.config_version,
                "Device config_version",
            ),
            "enabled": device.status != "Retired",
        }

    def build_rule(self, rule, device_id):
        """Share one mapped Rule payload across both sync paths."""

        if (
            rule.scope_type != "Device"
            or rule.scope_ref != device_id
        ):
            raise ValueError("Rule scope mismatch")

        if rule.schedule:
            raise ValueError(
                "Scheduled Rule mapping not implemented"
            )

        if rule.rule_type not in (
            "THRESHOLD",
            "STATE_CHANGE",
        ):
            raise ValueError("Unsupported rule type")

        device = self.repository.get_document(
            "Device",
            device_id,
        )
        mapping = self.get_device_mapping(device)
        threshold_rule = rule.rule_type == "THRESHOLD"

        return {
            "rule_id": get_rule_remote_id(rule),
            "version": positive_version(
                rule.version,
                "Rule version",
            ),
            "org_code": mapping.remote_org_code,
            "rule_type": rule.rule_type,
            "scope": {
                "type": "DEVICE",
                "ref": mapping.remote_device_id,
            },
            "data_point": (
                rule.data_point if threshold_rule else None
            ),
            "operator": (
                rule.operator if threshold_rule else None
            ),
            "threshold": (
                float(rule.threshold) if threshold_rule else None
            ),
            "duration_s": int(rule.duration_s or 0),
            "hysteresis": float(rule.hysteresis or 0),
            "clear_duration_s": int(rule.clear_duration_s or 0),
            "quiet_window_s": int(rule.quiet_window_s or 0),
            "event_type": (
                rule.event_type if not threshold_rule else None
            ),
            "open_limit_s": int(rule.open_limit_s or 0),
            "schedule": None,
            "severity": rule.severity,
            "auto_close": bool(rule.auto_close),
            "active": bool(rule.active),
        }

    def build_operations(self, device):
        """Validate the whole bundle before any remote write."""

        device_payload = self.build_device(device)
        profile = self.build_profile(device.device_profile)

        rules = [
            self.build_rule(rule, device.name)
            for rule in self.repository.get_device_rules(
                device.name
            )
        ]

        operations = [
            (
                "/api/v1/sync/profiles/"
                + quote(profile["profile_code"], safe=""),
                profile,
            ),
            (
                "/api/v1/sync/devices/"
                + quote(device_payload["device_id"], safe=""),
                device_payload,
            ),
        ]

        operations.extend(
            (
                "/api/v1/sync/rules/"
                + quote(rule["rule_id"], safe=""),
                rule,
            )
            for rule in rules
        )

        return operations

    def sync_device_bundle(self, device_id):
        device = self.repository.get_document(
            "Device",
            device_id,
        )

        if not eligible(device):
            return

        operations = self.build_operations(device)

        org_codes = {
            payload["org_code"]
            for _, payload in operations
            if "org_code" in payload
        }

        if len(org_codes) != 1:
            raise DataPlaneSyncError(
                "Device bundle must belong to one "
                "Data Plane Organization"
            )

        connection = load_sync_connection(
            frappe.conf,
            local_org=STAGING_ORG,
            remote_org=next(iter(org_codes)),
        )

        with requests.Session() as session:
            session.headers.update(connection.headers())

            try:
                for path, payload in operations:
                    send_put(
                        session,
                        connection.base_url,
                        path,
                        payload,
                    )
            except requests.RequestException:
                raise DataPlaneSyncError(
                    "Data Plane unavailable; "
                    "bundle sync may be partial"
                ) from None

        frappe.logger("nextgrp_iot_sync").info(
            "Device bundle sync completed: %s",
            device_id,
        )


def send_put(
    session,
    base,
    path,
    payload,
    *,
    require_applied_version=False,
):
    """Preserve the existing import path and Frappe logging."""

    return transport_send_put(
        session,
        base,
        path,
        payload,
        require_applied_version=require_applied_version,
        logger=frappe.logger("nextgrp_iot_sync"),
    )


def sync_device_bundle(device_id):
    """Preserve the existing background-job import path."""

    return DeviceSyncService().sync_device_bundle(device_id)