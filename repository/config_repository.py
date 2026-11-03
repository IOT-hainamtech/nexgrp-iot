
import frappe

from nextgrp.nextgrp_iot.service.integration_security import (
    is_data_plane,
)


class ConfigRepository:

    RULE_FIELDS = [
        "name",
        "rule_id",
        "rule_type",
        "scope_type",
        "scope_ref",
        "scope_profile",
        "scope_zone",
        "scope_site",
        "data_point",
        "event_type",
        "operator",
        "threshold",
        "duration_s",
        "hysteresis",
        "clear_duration_s",
        "quiet_window_s",
        "severity",
        "open_limit_s",
        "schedule",
        "auto_close",
        "active",
        "version",
    ]

    def get_document(self, doctype, name):
        if not name or not frappe.db.exists(
            doctype, name
        ):
            frappe.throw(
                f"{doctype} not found: {name}",
                frappe.DoesNotExistError,
            )

        doc = frappe.get_doc(doctype, name)

        # Data Plane access must already be scoped
        # by config_controller.get_device_config.
        if not is_data_plane():
            doc.check_permission("read")

        return doc

    def get_active_rules(self):
        # get_all bypasses DocType read permissions.
        # Only use it for scoped Data Plane requests.
        if is_data_plane():
            query = frappe.get_all
        else:
            query = frappe.get_list

        return query(
            "Rule Configuration",
            filters={"active": 1},
            fields=self.RULE_FIELDS,
            limit_page_length=1000,
        )
