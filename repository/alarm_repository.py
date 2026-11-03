
import frappe

from nextgrp.nextgrp_iot.service.integration_security import (
    is_data_plane,
    require_device_scope,
)


class AlarmRepository:

    def get_device(self, device_id):
        if not device_id or not frappe.db.exists(
            "Device", device_id
        ):
            frappe.throw(
                "Device not found",
                frappe.DoesNotExistError,
            )

        if is_data_plane():
            require_device_scope(device_id)

        doc = frappe.get_doc("Device", device_id)

        if not is_data_plane():
            doc.check_permission("read")

        return doc

    def get_rule(self, rule_id):
        if not rule_id or not frappe.db.exists(
            "Rule Configuration", rule_id
        ):
            frappe.throw(
                "Rule not found",
                frappe.DoesNotExistError,
            )

        doc = frappe.get_doc(
            "Rule Configuration",
            rule_id,
        )

        # AlarmService must still validate rule
        # scope, version, severity and active state.
        if not is_data_plane():
            doc.check_permission("read")

        return doc

    def get_alarm_for_update(self, alarm_key):
        if not isinstance(alarm_key, str) or not alarm_key:
            frappe.throw(
                "alarm_key is required",
                frappe.ValidationError,
            )

        rows = frappe.db.sql(
            """
            SELECT name
            FROM `tabAlarm`
            WHERE alarm_key = %s
            FOR UPDATE
            """,
            (alarm_key,),
            as_dict=True,
        )

        if not rows:
            return None

        doc = frappe.get_doc(
            "Alarm", rows[0].name
        )

        if is_data_plane():
            require_device_scope(doc.device)
        else:
            doc.check_permission("read")

        return doc

    def create_alarm(self, values):
        if not isinstance(values, dict):
            frappe.throw(
                "Alarm values must be an object",
                frappe.ValidationError,
            )

        if is_data_plane():
            require_device_scope(
                values.get("device")
            )

        doc = frappe.get_doc({
            "doctype": "Alarm",
            **values,
        })

        doc.insert(
            ignore_permissions=is_data_plane()
        )

        return doc

    def save_alarm(self, doc):
        if doc.doctype != "Alarm":
            frappe.throw(
                "Expected Alarm document",
                frappe.ValidationError,
            )

        if is_data_plane():
            require_device_scope(doc.device)
        else:
            doc.check_permission("write")

        doc.save(
            ignore_permissions=is_data_plane()
        )

        return doc
