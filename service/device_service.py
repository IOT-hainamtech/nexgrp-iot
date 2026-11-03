
import frappe

from nextgrp.nextgrp_iot.repository.device_repository import (
    DeviceRepository,
)


class DeviceService:

    VALID_STATUSES = {
        "Registered",
        "Active",
        "Maintenance",
        "Retired",
    }

    VALID_ONLINE_STATES = {
        "Unknown",
        "Online",
        "Offline",
    }

    def __init__(self):
        self.repository = DeviceRepository()

    def list_devices(
        self,
        site=None,
        zone=None,
        status=None,
        online_state=None,
        search=None,
        page=1,
        page_size=20,
    ):
        try:
            page = int(page)
            page_size = int(page_size)
        except (TypeError, ValueError):
            frappe.throw(
                "Invalid pagination parameters",
                frappe.ValidationError,
            )

        if page < 1 or not 1 <= page_size <= 100:
            frappe.throw(
                "Invalid page or page_size",
                frappe.ValidationError,
            )

        if status and status not in self.VALID_STATUSES:
            frappe.throw(
                "Invalid device status",
                frappe.ValidationError,
            )

        if online_state and online_state not in self.VALID_ONLINE_STATES:
            frappe.throw(
                "Invalid online state",
                frappe.ValidationError,
            )

        filters = {}

        if site:
            filters["site"] = site

        if zone:
            filters["zone"] = zone

        if status:
            filters["status"] = status

        if online_state:
            filters["online_state"] = online_state

        search = str(search).strip() if search else None

        if search and len(search) > 100:
            frappe.throw(
                "Search keyword too long",
                frappe.ValidationError,
            )

        return self.repository.list_devices(
            filters=filters,
            search=search,
            page=page,
            page_size=page_size,
        )

    def get_device(self, device_id):
        if not device_id:
            frappe.throw(
                "device_id is required",
                frappe.ValidationError,
            )

        return self.repository.get_device_by_id(
            str(device_id).strip()
        )
