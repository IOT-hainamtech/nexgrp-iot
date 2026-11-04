"""Validate changes to administrator-managed integration identifiers."""

import frappe

from nextgrp.nextgrp_iot.service.integration_mapping import DEVICE_FIELD, ORG_FIELD, RULE_FIELD, SITE_FIELD
from nextgrp.nextgrp_iot.service.integration_security import require_manager


FIELDS = {"Organization": ORG_FIELD, "Site": SITE_FIELD, "Device": DEVICE_FIELD, "Rule Configuration": RULE_FIELD}


def validate_mapping(doc, method=None):
    _validate_mapping(doc)
    if doc.doctype == "Rule Configuration":
        from nextgrp.nextgrp_iot.service.rule_sync_service import RuleSyncService
        try:
            RuleSyncService().validate_rule_save(doc)
        except ValueError as exc:
            frappe.throw(str(exc), frappe.ValidationError)


def _validate_mapping(doc):
    field = FIELDS[doc.doctype]
    value = doc.get(field)
    old = doc.get_doc_before_save()
    previous = old.get(field) if old else None
    scope_fields = {"Site": ("organization",), "Device": ("site",), "Rule Configuration": ("scope_type", "scope_ref")}
    scope_changed = old and any(doc.get(key) != old.get(key) for key in scope_fields.get(doc.doctype, ()))
    if (value or "") == (previous or "") and not (value and scope_changed):
        return
    require_manager()
    if value and (not isinstance(value, str) or len(value) > 140 or any(ord(c) < 32 for c in value)):
        frappe.throw("Mã liên kết Data Plane không hợp lệ (tối đa 140 ký tự).", frappe.ValidationError)
    value = value.strip() if value else None
    doc.set(field, value or None)
    if not value:
        return

    filters = {field: value, "name": ["!=", doc.name or ""]}
    if doc.doctype == "Site":
        if not frappe.db.get_value("Organization", doc.organization, ORG_FIELD):
            frappe.throw("Hãy nhập mã Data Plane trên đơn vị trước.", frappe.ValidationError)
        filters["organization"] = doc.organization
    elif doc.doctype == "Device":
        organization = frappe.db.get_value("Site", doc.site, "organization")
        if not organization or not frappe.db.get_value("Organization", organization, ORG_FIELD):
            frappe.throw("Hãy nhập mã Data Plane trên đơn vị của địa điểm trước.", frappe.ValidationError)
        if not frappe.db.get_value("Site", doc.site, SITE_FIELD):
            frappe.throw("Hãy nhập mã Data Plane trên địa điểm trước.", frappe.ValidationError)
        filters["site"] = ["in", frappe.get_all("Site", filters={"organization": organization}, pluck="name")]
    elif doc.doctype == "Rule Configuration":
        if doc.scope_type != "Device" or not doc.scope_ref:
            frappe.throw("Liên kết Data Plane hiện áp dụng cho quy tắc của một thiết bị.", frappe.ValidationError)
        if not frappe.db.get_value("Device", doc.scope_ref, DEVICE_FIELD):
            frappe.throw("Hãy nhập mã Data Plane trên thiết bị trước.", frappe.ValidationError)
        # A remote rule ID is unique within its Organization.
        organization = frappe.db.get_value("Site", frappe.db.get_value("Device", doc.scope_ref, "site"), "organization")
        sites = frappe.get_all("Site", filters={"organization": organization}, pluck="name")
        devices = frappe.get_all("Device", filters={"site": ["in", sites]}, pluck="name")
        filters.update(scope_type="Device", scope_ref=["in", devices])
    if frappe.db.exists(doc.doctype, filters):
        frappe.throw("Mã Data Plane này đã được liên kết với bản ghi khác trong cùng phạm vi.", frappe.ValidationError)
