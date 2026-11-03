
import frappe
from frappe.utils import now_datetime

from nextgrp.nextgrp_iot.service.integration_security import (
    is_data_plane,
    require_device_scope,
)


class StatusRepository:

    def update_status(self, device_id, state):
        if not isinstance(device_id, str) or not device_id.strip():
            frappe.throw(
                "device_id is required",
                frappe.ValidationError,
            )

        device_id = device_id.strip()

        if not isinstance(state, str):
            frappe.throw(
                "state is required",
                frappe.ValidationError,
            )

        normalized = state.strip().lower()

        if normalized not in ("online", "offline"):
            frappe.throw(
                "state must be online or offline",
                frappe.ValidationError,
            )

        new_state = (
            "Online"
            if normalized == "online"
            else "Offline"
        )

       
        if is_data_plane():
            require_device_scope(device_id)

        rows = frappe.db.sql(
            """
            SELECT name
            FROM `tabDevice`
            WHERE name = %s
            FOR UPDATE
            """,
            (device_id,),
            as_dict=True,
        )

        if not rows:
            frappe.throw(
                "Device not found",
                frappe.DoesNotExistError,
            )

        doc = frappe.get_doc("Device", device_id)

        if not is_data_plane():
            doc.check_permission("write")

        previous_state = doc.online_state or "Unknown"
        changed = previous_state != new_state

        if changed:
            doc.online_state = new_state

        # Online reports refresh last_seen even when
        # the connection state has not changed.
        if new_state == "Online":
            doc.last_seen = now_datetime()

        if changed or new_state == "Online":
            doc.save(
                ignore_permissions=is_data_plane()
            )

        return {
            "device_id": doc.device_id or doc.name,
            "previous_state": previous_state,
            "online_state": new_state,
            "last_seen": (
                str(doc.last_seen)
                if doc.last_seen
                else None
            ),
            "changed": changed,
        }
