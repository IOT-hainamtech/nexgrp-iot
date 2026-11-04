"""Synchronize Rule configuration through immutable snapshots."""

from urllib.parse import quote
from functools import partial
import json

import frappe
import requests

from nextgrp.nextgrp_iot.repository.device_sync_repository import (
    DeviceSyncRepository,
    RULE_JOB,
)

from .device_sync_service import (
    STAGING_ORG,
    DataPlaneSyncError,
    DeviceSyncService,
    eligible,
    positive_version,
    send_put,
)
from .rule_sync_snapshot import RuleSyncSnapshot
from .sync_transport import load_sync_connection, DataPlaneVersionConflict
from .integration_mapping import RULE_FIELD


class RuleSyncService:
    def __init__(self, repository=None):
        self.repository = (
            repository
            if repository is not None
            else DeviceSyncRepository()
        )

    def validate_rule_save(self, doc):
        """Version semantic changes; defer persistence until the Rule exists.

        Existing validate hooks already run under Frappe's document lock.
        The before_commit callback keeps Rule and outbox in one transaction.
        """
        if not frappe.db.exists("DocType", RULE_JOB):
            return
        old = doc.get_doc_before_save()
        if old and old.scope_type == "Device" and old.scope_ref:
            previous_device = self.repository.get_document("Device", old.scope_ref)
            if eligible(previous_device) and any(doc.get(field) != old.get(field) for field in ("rule_id", RULE_FIELD, "scope_type", "scope_ref")):
                frappe.throw("Không đổi định danh/phạm vi của Rule đang đồng bộ. Hãy tạo Rule mới.", frappe.ValidationError)
        if doc.scope_type != "Device" or not doc.scope_ref:
            return
        device = self.repository.get_document("Device", doc.scope_ref)
        if not eligible(device):
            return
        previous = positive_version(old.version, "Previous Rule version") if old else 0
        # Version is owned by the save lifecycle, not by arbitrary form input.
        doc.version = previous or 1
        builder = DeviceSyncService(self.repository)
        current = builder.build_rule(doc, doc.scope_ref)
        before = builder.build_rule(old, old.scope_ref) if old else None
        changed = before is None or current != before
        doc.version = previous + 1 if changed else previous
        if changed:
            frappe.db.before_commit.add(partial(self._persist_saved_rule, doc.name, doc.version))

    def _persist_saved_rule(self, rule_id, minimum_version):
        # Re-read the saved row, not the mutable document captured by validate.
        # This also ignores changes rolled back to a savepoint before commit.
        if not frappe.db.exists("Rule Configuration", rule_id):
            return
        snapshot = self.build_snapshot(rule_id)
        if snapshot.to_payload()["version"] < minimum_version:
            return
        job_id = self.repository.store_rule_job(snapshot)
        frappe.db.after_commit.add(partial(enqueue_rule_job, job_id))

    def replay_job(self, job_id):
        """Explicit manager action; never rebuild a historical payload."""
        job = self.repository.get_rule_job(job_id, for_update=True)
        snapshot = RuleSyncSnapshot.from_record(json.loads(job.snapshot_json))
        if snapshot.checksum != job.snapshot_checksum:
            raise ValueError("Stored Rule snapshot checksum mismatch")
        self.repository.reset_rule_job(job_id)
        frappe.db.after_commit.add(partial(enqueue_rule_job, job_id))
        return {"job_id": job_id, "status": "Pending", "version": snapshot.to_payload()["version"]}

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


def enqueue_rule_job(job_id):
    """Called after commit; a queue outage leaves the durable job recoverable."""
    try:
        frappe.enqueue(
            "nextgrp.nextgrp_iot.service.rule_sync_service.run_rule_job",
            queue="short", timeout=120, job_id="iot-rule-" + job_id,
            deduplicate=True, sync_job_id=job_id,
        )
    except Exception:
        frappe.logger("nextgrp_iot_sync").warning("Rule job remains pending; recovery will retry enqueue")


def recover_pending_jobs():
    if not frappe.db.exists("DocType", RULE_JOB):
        return
    for job_id in DeviceSyncRepository().pending_rule_jobs():
        enqueue_rule_job(job_id)


def run_rule_job(sync_job_id):
    """Worker-only transaction boundary; never called during validation."""
    repo = DeviceSyncRepository()
    claim = repo.claim_rule_job(sync_job_id)
    frappe.db.commit()
    if claim is None:
        return
    ack, error = None, None
    try:
        snapshot = RuleSyncSnapshot.from_record(json.loads(claim["record_json"]))
        if snapshot.checksum != claim["checksum"]:
            raise ValueError("Stored Rule snapshot checksum mismatch")
        ack = RuleSyncService(repo).sync_snapshot(snapshot)
        status = "Applied"
    except (ValueError, DataPlaneVersionConflict):
        status, error = "Conflict", "Snapshot/version conflict; reconcile before an explicit replay"
    except DataPlaneSyncError as exc:
        status, error = "Failed", str(exc)
    except Exception:
        status, error = "Failed", "Unexpected synchronization error"
    finished = repo.finish_rule_job(sync_job_id, claim["token"], status, ack=ack, error=error)
    if finished and status == "Applied":
        frappe.db.after_commit.add(recover_pending_jobs)
    frappe.db.commit()
    return {"job_id": sync_job_id, "status": repo.get_rule_job(sync_job_id).status}
