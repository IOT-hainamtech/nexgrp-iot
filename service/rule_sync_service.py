"""Synchronize one Rule using the shared mapped payload builder."""

from urllib.parse import quote, urlsplit

import frappe
import requests

from nextgrp.nextgrp_iot.repository.device_sync_repository import (
    DeviceSyncRepository,
)

from .device_sync_service import (
    STAGING_ORG,
    DataPlaneSyncError,
    DeviceSyncService,
    positive_version,
    send_put,
)


class RuleSyncService:
    def __init__(self, repository=None):
        self.repository = (
            repository
            if repository is not None
            else DeviceSyncRepository()
        )

    def build_operation(self, rule_id):
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise ValueError("Rule ID is required")

        rule = self.repository.get_document(
            "Rule Configuration",
            rule_id.strip(),
        )

        if rule.scope_type != "Device" or not rule.scope_ref:
            raise ValueError(
                "Only Device-scoped Rules can be synchronized"
            )

        payload = DeviceSyncService(
            self.repository
        ).build_rule(
            rule,
            rule.scope_ref,
        )

        path = (
            "/api/v1/sync/rules/"
            + quote(payload["rule_id"], safe="")
        )

        return path, payload

    def sync_rule(self, rule_id):
        path, payload = self.build_operation(rule_id)

        base = str(
            frappe.conf.get("nextwireless_dp_base_url") or ""
        ).rstrip("/")
        token = frappe.conf.get(
            "nextwireless_dp_inbound_token"
        )
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
            or frappe.conf.get(
                "nextwireless_dp_org_code"
            ) != STAGING_ORG
        ):
            raise DataPlaneSyncError(
                "Data Plane site configuration missing or invalid"
            )

        with requests.Session() as session:
            session.headers.update({
                "Authorization": f"Bearer {token.strip()}",
                "X-Org-Code": payload["org_code"],
                "Accept": "application/json",
                "ngrok-skip-browser-warning": "true",
            })

            try:
                ack = send_put(
                    session,
                    base,
                    path,
                    payload,
                )
            except requests.RequestException:
                raise DataPlaneSyncError(
                    "Data Plane unavailable; Rule sync not confirmed"
                ) from None

        try:
            applied_version = positive_version(
                ack.get("applied_version"),
                "applied_version",
            )
        except ValueError:
            raise DataPlaneSyncError(
                "Rule ACK missing applied_version; "
                "verify Data Plane before retrying"
            ) from None

        if applied_version != payload["version"]:
            raise DataPlaneSyncError(
                "Rule ACK version mismatch"
            )

        return {
            "rule_id": rule_id.strip(),
            "data_plane_rule_id": payload["rule_id"],
            "status": ack["status"],
            "applied_version": applied_version,
        }


def sync_rule(rule_id):
    """Trusted entry point; HTTP callers use the existing controller."""

    return RuleSyncService().sync_rule(rule_id)