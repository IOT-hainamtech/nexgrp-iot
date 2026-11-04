"""Install mapping form fields without changing existing DocType permissions."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from nextgrp.nextgrp_iot.service.integration_mapping import DEVICE_FIELD, ORG_FIELD, RULE_FIELD, SITE_FIELD


MAPPING_FIELDS = {
    "Organization": (ORG_FIELD, "Mã đơn vị bên Data Plane"),
    "Site": (SITE_FIELD, "Mã địa điểm bên Data Plane"),
    "Device": (DEVICE_FIELD, "Mã thiết bị bên Data Plane"),
    "Rule Configuration": (RULE_FIELD, "Mã quy tắc bên Data Plane"),
}


def ensure_mapping_fields():
    for doctype, (fieldname, label) in MAPPING_FIELDS.items():
        if not frappe.db.exists("DocType", doctype):
            continue
        definitions = [
            {"fieldname": "custom_data_plane_section", "fieldtype": "Section Break", "label": "Liên kết Data Plane"},
            {
                "fieldname": fieldname,
                "fieldtype": "Data",
                "label": label,
                "length": 140,
                "no_copy": 1,
                "description": "Nhập mã tương ứng đã thống nhất với bên Data Plane. Chỉ quản trị viên được thay đổi.",
            },
        ]
        meta = frappe.get_meta(doctype)
        if not meta.custom:
            definitions[0]["insert_after"] = "append"
            definitions[1]["insert_after"] = "custom_data_plane_section"
            create_custom_fields({doctype: definitions}, update=False)
            continue

        # Site/Device/Rule are site-owned Custom DocTypes: extend their actual
        # DocFields instead of attaching Custom Field records intended for standard DocTypes.
        doc = frappe.get_doc("DocType", doctype)
        changed = False
        insertion = len(doc.fields)
        while insertion and doc.fields[insertion - 1].fieldtype == "Tab Break":
            insertion -= 1
        for definition in definitions:
            if any(row.fieldname == definition["fieldname"] for row in doc.fields):
                continue
            row = doc.append("fields", definition)
            doc.fields.remove(row)
            doc.fields.insert(insertion, row)
            insertion += 1
            changed = True
        if changed:
            doc.save(ignore_permissions=True)
            frappe.clear_cache(doctype=doctype)

    ensure_rule_sync_schema()


def ensure_rule_sync_schema():
    """Install configuration outbox and recovery job through the existing hook."""
    if not frappe.db.exists("DocType", "Rule Configuration"):
        return
    name = "IoT Rule Sync Job"
    definitions = [
        ("job_key", "Data"), ("local_rule_id", "Data"), ("local_org_code", "Data"),
        ("remote_org_code", "Data"), ("remote_rule_id", "Data"), ("rule_version", "Int"),
        ("snapshot_json", "Long Text"), ("snapshot_checksum", "Data"),
        ("status", "Select"), ("attempts", "Int"), ("ack_json", "Long Text"),
        ("last_error", "Small Text"), ("processing_token", "Data"),
        ("lease_until", "Datetime"), ("next_attempt_at", "Datetime"),
    ]
    if not frappe.db.exists("DocType", name):
        fields = [{"fieldname": field, "label": field.replace("_", " ").title(),
                   "fieldtype": kind, "read_only": 1} for field, kind in definitions]
        for row in fields:
            if row["fieldname"] in {field for field, _ in definitions[:9]}:
                row["reqd"] = 1
            if row["fieldname"] == "status":
                row.update(options="Pending\nProcessing\nApplied\nFailed\nConflict\nStopped", default="Pending")
            if row["fieldname"] == "job_key":
                row["unique"] = 1
            if row["fieldname"] in ("local_rule_id", "rule_version", "status"):
                row["in_list_view"] = 1
        frappe.get_doc({
            "doctype": "DocType", "name": name, "custom": 1,
            "module": frappe.get_meta("Rule Configuration").module,
            "autoname": "field:job_key", "title_field": "local_rule_id",
            "fields": fields,
            "permissions": [{"role": "System Manager", "read": 1, "report": 1}],
        }).insert(ignore_permissions=True)
        frappe.clear_cache(doctype=name)
    else:
        meta = frappe.get_meta(name)
        for field, kind in definitions:
            existing = meta.get_field(field)
            if existing is None or existing.fieldtype != kind:
                frappe.throw("IoT Rule Sync Job schema mismatch: " + field)
    frappe.db.add_index(name, ["local_rule_id", "rule_version"], "iot_rule_sync_version")
    frappe.db.add_index(name, ["status", "creation"], "iot_rule_sync_pending")
    # Existing after_migrate runs after Frappe's hook-job synchronization.
    # Re-register this module-owned recovery job without editing hooks.py.
    from frappe.core.doctype.scheduled_job_type.scheduled_job_type import insert_single_event
    insert_single_event("Cron", "nextgrp.nextgrp_iot.service.rule_sync_service.recover_pending_jobs", "* * * * *")

