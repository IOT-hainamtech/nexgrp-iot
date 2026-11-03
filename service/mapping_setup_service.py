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

