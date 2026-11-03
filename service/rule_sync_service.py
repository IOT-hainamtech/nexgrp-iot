"""Synchronize Rule configuration through immutable snapshots."""

from urllib.parse import quote

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
from .rule_sync_snapshot import RuleSyncSnapshot
from .sync_transport import load_sync_connection


class RuleSyncService:
    def __init__(self, repository=None):
        self.repository = (
            repository
            if repository is not None
            else DeviceSyncRepository()
        )

    def build_operation(self, rule_id):
        """Build the current Rule configuration using the shared builder."""

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

    def build_snapshot(self, rule_id):
        """Capture the current configuration before sending it."""

        path, payload = self.build_operation(rule_id)

        return RuleSyncSnapshot.capture(
            local_rule_id=rule_id.strip(),
            local_org_code=STAGING_ORG,
            path=path,
            payload=payload,
        )

    def sync_rule(self, rule_id):
        """Preserve the existing entry point for current configuration."""

        snapshot = self.build_snapshot(rule_id)
        return self.sync_snapshot(snapshot)

    def sync_snapshot(self, snapshot):
        """Send the captured configuration without rereading the Rule."""

        if not isinstance(snapshot, RuleSyncSnapshot):
            raise ValueError("A RuleSyncSnapshot is required")

        # Validation occurs before opening an HTTP session.
        payload = snapshot.to_payload()

        connection = load_sync_connection(
            frappe.conf,
            local_org=snapshot.local_org_code,
            remote_org=payload["org_code"],
        )

        with requests.Session() as session:
            session.headers.update(connection.headers())

            try:
                ack = send_put(
                    session,
                    connection.base_url,
                    snapshot.path,
                    payload,
                    require_applied_version=True,
                )
            except requests.RequestException:
                raise DataPlaneSyncError(
                    "Data Plane unavailable; Rule sync not confirmed"
                ) from None

        return {
            "rule_id": snapshot.local_rule_id,
            "data_plane_rule_id": payload["rule_id"],
            "status": ack["status"],
            "applied_version": positive_version(
                ack["applied_version"],
                "applied_version",
            ),
        }


def sync_rule(rule_id):
    """Trusted entry point; HTTP callers use the existing controller."""

    return RuleSyncService().sync_rule(rule_id)