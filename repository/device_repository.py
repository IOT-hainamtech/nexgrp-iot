
import frappe


class DeviceRepository:

    LIST_FIELDS = [
        "name",
        "device_id",
        "device_profile",
        "site",
        "zone",
        "status",
        "online_state",
        "last_seen",
        "serial_number",
        "fw_version",
        "modified",
    ]

    DETAIL_FIELDS = [
        "name",
        "device_id",
        "device_profile",
        "site",
        "zone",
        "status",
        "online_state",
        "last_seen",
        "serial_number",
        "mac",
        "manufacturer",
        "model",
        "fw_version",
        "connectivity_type",
        "config_version",
        "parent_device",
        "installation_position",
        "commissioned_at",
        "retired_at",
        "maintenance_interval_days",
    ]

    def list_devices(
        self,
        filters=None,
        search=None,
        page=1,
        page_size=20,
    ):
        filters = filters or {}
        or_filters = None

        if search:
            keyword = f"%{search}%"

            or_filters = [
                ["Device", "device_id", "like", keyword],
                ["Device", "serial_number", "like", keyword],
            ]

        devices = frappe.get_list(
            "Device",
            filters=filters,
            or_filters=or_filters,
            fields=self.LIST_FIELDS,
            order_by="modified desc, name asc",
            start=(page - 1) * page_size,
            page_length=page_size + 1,
        )

        has_more = len(devices) > page_size

        return {
            "data": devices[:page_size],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "has_more": has_more,
            },
        }

    def get_device_by_id(self, device_id):
        device = frappe.get_doc("Device", device_id)

        device.check_permission("read")

        return {
            field: device.get(field)
            for field in self.DETAIL_FIELDS
        }
