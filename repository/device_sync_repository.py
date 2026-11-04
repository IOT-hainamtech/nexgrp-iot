"""Internal reads for the trusted Device sync worker, not a public API."""

import hashlib
import json
from uuid import uuid4

import frappe
from frappe.utils import add_to_date, now_datetime, get_datetime


RULE_JOB = "IoT Rule Sync Job"


class DeviceSyncRepository:
    def get_document(self, doctype, name):
        return frappe.get_doc(doctype, name)

    def get_device_rules(self, device_id):
       
        names = frappe.get_all(
            "Rule Configuration",
            filters={"scope_type": "Device", "scope_ref": device_id},
            pluck="name",
            order_by="name asc",
        )
        return [self.get_document("Rule Configuration", name) for name in names]

    def store_rule_job(self, snapshot):
        """Participate in the caller's transaction; never commit here."""
        snapshot.validate()
        payload = snapshot.to_payload()
        identity = json.dumps([snapshot.local_org_code, payload["org_code"], payload["rule_id"], payload["version"]])
        name = hashlib.sha256(identity.encode()).hexdigest()
        record = json.dumps(snapshot.to_record(), sort_keys=True, allow_nan=False)
        savepoint = "rule_sync_" + uuid4().hex
        frappe.db.savepoint(savepoint)
        try:
            frappe.get_doc({
                "doctype": RULE_JOB, "job_key": name,
                "local_rule_id": snapshot.local_rule_id,
                "local_org_code": snapshot.local_org_code,
                "remote_org_code": payload["org_code"],
                "remote_rule_id": payload["rule_id"],
                "rule_version": payload["version"],
                "snapshot_json": record, "snapshot_checksum": snapshot.checksum,
                "status": "Pending", "attempts": 0,
            }).insert(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            frappe.db.rollback(save_point=savepoint)
            existing = frappe.get_doc(RULE_JOB, name, for_update=True)
            if existing.snapshot_json != record or existing.snapshot_checksum != snapshot.checksum:
                raise ValueError("Rule/version already has a different synchronization snapshot") from None
        finally:
            frappe.db.release_savepoint(savepoint)
        return name

    def get_rule_job(self, name, for_update=False):
        return frappe.get_doc(RULE_JOB, name, for_update=for_update)

    def pending_rule_jobs(self):
        # Blocked versions must not occupy the first page forever and starve
        # unrelated Rules. A lease/retry deadline is checked again when claiming.
        rows = frappe.db.sql(
            """SELECT job.name FROM `tabIoT Rule Sync Job` job
            WHERE (
                job.status = 'Pending'
                OR (job.status = 'Failed' AND
                    (job.next_attempt_at IS NULL OR job.next_attempt_at <= %(now)s))
                OR (job.status = 'Processing' AND
                    (job.lease_until IS NULL OR job.lease_until <= %(now)s))
            ) AND NOT EXISTS (
                SELECT 1 FROM `tabIoT Rule Sync Job` older
                WHERE older.local_rule_id = job.local_rule_id
                AND older.rule_version < job.rule_version
                AND older.status != 'Applied'
            ) ORDER BY job.creation ASC LIMIT 100""",
            {"now": now_datetime()}, as_dict=True,
        )
        return [row.name for row in rows]

    def claim_rule_job(self, name):
        """Lease one version; serialize sends for the same local Rule."""
        job = self.get_rule_job(name)
        # Lock the Rule before its jobs, consistently across every worker.
        rule = frappe.db.get_value("Rule Configuration", job.local_rule_id, "name", for_update=True)
        job = self.get_rule_job(name, for_update=True)
        now = now_datetime()
        if job.status in ("Applied", "Conflict", "Stopped"):
            return None
        if not rule:
            frappe.db.set_value(RULE_JOB, name, {"status": "Stopped", "last_error": "Local Rule no longer exists"})
            return None
        if job.status == "Processing" and job.lease_until and get_datetime(job.lease_until) > now:
            return None
        if job.next_attempt_at and get_datetime(job.next_attempt_at) > now:
            return None
        if int(job.attempts or 0) >= 5:
            frappe.db.set_value(RULE_JOB, name, {"status": "Stopped", "last_error": "Retry limit reached; reconcile before retrying"})
            return None
        older = frappe.get_all(RULE_JOB, filters={
            "local_rule_id": job.local_rule_id,
            "rule_version": ["<", job.rule_version], "status": ["!=", "Applied"],
        }, limit_page_length=1, pluck="name")
        if older:
            return None
        token = uuid4().hex
        attempts = int(job.attempts or 0) + 1
        frappe.db.set_value(RULE_JOB, name, {
            "status": "Processing", "attempts": attempts, "processing_token": token,
            "lease_until": add_to_date(now, seconds=180), "next_attempt_at": None,
        })
        return {"record_json": job.snapshot_json, "checksum": job.snapshot_checksum,
                "token": token, "attempts": attempts}

    def reset_rule_job(self, name):
        job = self.get_rule_job(name, for_update=True)
        if job.status == "Processing" and job.lease_until and get_datetime(job.lease_until) > now_datetime():
            raise ValueError("Rule synchronization is still running")
        frappe.db.set_value(RULE_JOB, name, {
            "status": "Pending", "attempts": 0, "next_attempt_at": None,
            "processing_token": None, "lease_until": None, "last_error": None,
        })

    def finish_rule_job(self, name, token, status, ack=None, error=None):
        job = self.get_rule_job(name, for_update=True)
        if job.status != "Processing" or job.processing_token != token:
            return False
        if status not in ("Applied", "Failed", "Conflict"):
            raise ValueError("Invalid Rule sync completion status")
        attempts = int(job.attempts or 0)
        if status == "Failed" and attempts >= 5:
            status = "Stopped"
        delay = min(900, 30 * 2 ** max(attempts - 1, 0))
        frappe.db.set_value(RULE_JOB, name, {
            "status": status,
            "ack_json": json.dumps(ack, allow_nan=False) if ack is not None else None,
            "last_error": error,
            "processing_token": None, "lease_until": None,
            "next_attempt_at": add_to_date(now_datetime(), seconds=delay) if status == "Failed" else None,
        })
        return True
