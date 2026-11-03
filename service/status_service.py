
import frappe
from frappe.realtime import get_doctype_room

from nextgrp.nextgrp_iot.repository.status_repository import (
    StatusRepository,
)


class StatusService:

    def __init__(self):
        self.repository = StatusRepository()

    def update_status(self, device_id, state):
        device_id = str(device_id or "").strip()
        state = str(state or "").strip().capitalize()

        if not device_id:
            frappe.throw(
                "device_id is required",
                frappe.ValidationError,
            )

        if state not in {"Online", "Offline"}:
            frappe.throw(
                "state must be online or offline",
                frappe.ValidationError,
            )

        result = self.repository.update_status(
            device_id=device_id,
            state=state,
        )

        if result["changed"]:
            frappe.publish_realtime(
                event="nextgrp_iot_device_status",
                message={
                    "device_id": result["device_id"],
                    "online_state": result["online_state"],
                    "last_seen": result["last_seen"],
                },
                room=get_doctype_room("Device"),
                after_commit=True,
            )

        return result
