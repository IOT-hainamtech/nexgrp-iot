"""One-time transfer of the confirmed S1/S6 file mapping into editable form fields."""

import frappe

from nextgrp.nextgrp_iot.service.integration_mapping import DEVICE_FIELD, ORG_FIELD, RULE_FIELD, SITE_FIELD
from nextgrp.nextgrp_iot.service.mapping_setup_service import ensure_mapping_fields


def execute():
    ensure_mapping_fields()
    organization, site = "NW-LOCAL-ORG-001", "NW-LOCAL-SITE-001"
    if not frappe.db.exists("Organization", organization):
        return
    if frappe.db.get_value("Site", site, "organization") != organization:
        return
    rows = [("Organization", organization, ORG_FIELD, "hainam"), ("Site", site, SITE_FIELD, "hq")]
    for device, remote, rule, remote_rule in (
        ("NW-IT-TH001", "TH-001", "NW-IT-RULE-S1", "S1_TEMP_HIGH"),
        ("NW-IT-DR001", "DR-001", "NW-IT-RULE-S6", "S6_DOOR_OPEN"),
    ):
        if frappe.db.get_value("Device", device, "site") != site:
            continue
        rows.append(("Device", device, DEVICE_FIELD, remote))
        scope = frappe.db.get_value("Rule Configuration", rule, ["scope_type", "scope_ref"])
        if scope and tuple(scope) == ("Device", device):
            rows.append(("Rule Configuration", rule, RULE_FIELD, remote_rule))
    # Preserve values already entered on forms, including custom site deployments.
    for doctype, name, field, value in rows:
        if not frappe.db.get_value(doctype, name, field):
            frappe.db.set_value(doctype, name, field, value, update_modified=False)
