
import frappe

from nextgrp.nextgrp_iot.service.device_service import (
    DeviceService,
)


def _check_device_read_permission():
    if not frappe.has_permission(
        "Device",
        ptype="read",
    ):
        frappe.throw(
            "Permission denied",
            frappe.PermissionError,
        )


@frappe.whitelist(methods=["GET"])
def list_devices(
    site=None,
    zone=None,
    status=None,
    online_state=None,
    search=None,
    page=1,
    page_size=20,
):
    _check_device_read_permission()

    return DeviceService().list_devices(
        site=site,
        zone=zone,
        status=status,
        online_state=online_state,
        search=search,
        page=page,
        page_size=page_size,
    )


@frappe.whitelist(methods=["GET"])
def get_device(device_id=None):
    _check_device_read_permission()

    return DeviceService().get_device(device_id)
