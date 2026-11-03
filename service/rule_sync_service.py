"""IF-03 Rule-only synchronization using the saved integration identities."""

from urllib.parse import quote, urlsplit

import frappe
import requests

from nextgrp.nextgrp_iot.repository.device_sync_repository import DeviceSyncRepository
from nextgrp.nextgrp_iot.service.device_sync_service import (
    STAGING_ORG,
    DataPlaneSyncError,
    DeviceSyncService,
    eligible,
    positive_version,
    send_put,
)
from nextgrp.nextgrp_iot.service.integration_mapping import RULE_FIELD, get_device_read_mapping


class RuleSyncService:
    def __init__(self, repository=None):
        self.repository = repository if repository is not None else DeviceSyncRepository()

    def build_operation(self, rule_id):
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise ValueError("Rule ID is required")
        rule = self.repository.get_document("Rule Configuration", rule_id.strip())
        if rule.scope_type != "Device" or not rule.scope_ref:
            raise ValueError("Only Device-scoped Rules can be synchronized")
        device = self.repository.get_document("Device", rule.scope_ref)
        site = self.repository.get_document("Site", device.site)
        if not eligible(device) or site.organization != STAGING_ORG:
            raise ValueError("Rule outside staging Organization/Site/Profile")

        # The Rule conditions and version remain authoritative in nextGRP.
        payload = DeviceSyncService(self.repository).build_rule(rule, device.name)
        mapping = get_device_read_mapping(device, site.organization)
        remote_rule = rule.get(RULE_FIELD)
        if mapping is None or not isinstance(remote_rule, str) or not remote_rule:
            raise ValueError("Rule and Device Data Plane mappings are required")
        if remote_rule.strip() != remote_rule or len(remote_rule) > 140 or any(ord(c) < 32 for c in remote_rule):
            raise ValueError("Invalid Data Plane Rule mapping")
        payload.update({
            "rule_id": remote_rule,
            "org_code": mapping.remote_org_code,
            "scope": {"type": "DEVICE", "ref": mapping.remote_device_id},
        })
        return "/api/v1/sync/rules/" + quote(remote_rule, safe=""), payload

    def sync_rule(self, rule_id):
        # Validate mapping and the entire payload before making a remote write.
        path, payload = self.build_operation(rule_id)
        base = str(frappe.conf.get("nextwireless_dp_base_url") or "").rstrip("/")
        token = frappe.conf.get("nextwireless_dp_inbound_token")
        parsed = urlsplit(base)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or not isinstance(token, str) or not token.strip()
            or frappe.conf.get("nextwireless_dp_org_code") != STAGING_ORG
        ):
            raise DataPlaneSyncError("Data Plane site configuration missing or invalid")

        with requests.Session() as session:
            session.headers.update({
                "Authorization": f"Bearer {token.strip()}",
                "X-Org-Code": payload["org_code"],
                "Accept": "application/json",
                "ngrok-skip-browser-warning": "true",
            })
            try:
                ack = send_put(session, base, path, payload)
            except requests.RequestException:
                raise DataPlaneSyncError("Data Plane unavailable; Rule sync not confirmed") from None

        # Do not report the local version as remotely applied without an ACK.
        try:
            applied_version = positive_version(ack.get("applied_version"), "applied_version")
        except ValueError:
            raise DataPlaneSyncError("Rule ACK missing applied_version; verify Data Plane before retrying") from None
        if applied_version != payload["version"]:
            raise DataPlaneSyncError("Rule ACK version mismatch")
        return {
            "rule_id": rule_id.strip(),
            "data_plane_rule_id": payload["rule_id"],
            "status": ack["status"],
            "applied_version": applied_version,
        }


def sync_rule(rule_id):
    """Trusted CLI/worker entry point; HTTP callers must use the controller."""
    return RuleSyncService().sync_rule(rule_id)
