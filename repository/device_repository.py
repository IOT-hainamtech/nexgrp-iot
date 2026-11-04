
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
        filters = dict(filters or {})
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

        result = {
            field: device.get(field)
            for field in self.DETAIL_FIELDS
        }
        return result

    def connection_counts(self):
        # IF-04 updates the stored state; Dashboard reads it without polling Data Plane.
        devices = frappe.get_list(
            "Device", fields=["name", "online_state"], page_length=0,
        )
        counts = {"total": len(devices), "online": 0, "offline": 0, "unknown": 0}
        for row in devices:
            state = str(row.get("online_state") or "unknown").lower()
            counts[state if state in ("online", "offline") else "unknown"] += 1
        return counts
