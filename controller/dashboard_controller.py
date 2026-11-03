
import frappe

from nextgrp.nextgrp_iot.service.dashboard_service import (
    DashboardService,
)


@frappe.whitelist(methods=["GET"])
def get_summary():
    if not frappe.has_permission("Device", ptype="read"):
        frappe.throw(
            "Permission denied",
            frappe.PermissionError,
        )

    return DashboardService().get_summary()
