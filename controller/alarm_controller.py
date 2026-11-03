
import json

import frappe

from nextgrp.nextgrp_iot.service.alarm_service import (
    AlarmService,
)
from nextgrp.nextgrp_iot.service.integration_security import (
    is_data_plane,
    require_device_scope,
    require_manager_or_data_plane,
)


@frappe.whitelist(methods=["POST"])
def receive_alarm(payload=None):
    require_manager_or_data_plane()

    if is_data_plane():
        if isinstance(payload, str):
            if len(payload) > 65536:
                frappe.throw(
                    "Alarm payload too large",
                    frappe.ValidationError,
                )

            try:
                data = json.loads(payload)
            except (TypeError, ValueError):
                frappe.throw(
                    "Invalid alarm JSON",
                    frappe.ValidationError,
                )
        elif isinstance(payload, dict):
            data = payload
        else:
            frappe.throw(
                "Invalid alarm payload",
                frappe.ValidationError,
            )

        if not isinstance(data, dict):
            frappe.throw(
                "Alarm payload must be an object",
                frappe.ValidationError,
            )

        require_device_scope(data.get("device_id"))

    return AlarmService().process(payload)
