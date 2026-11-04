
import frappe

from nextgrp.nextgrp_iot.repository.device_repository import DeviceRepository


class DashboardRepository:

    def get_summary(self):
        return {
            "sites": frappe.db.count("Site"),
            "zones": frappe.db.count("Zone"),
            "devices": DeviceRepository().connection_counts(),
            "alarms": {
                "total": frappe.db.count("Alarm"),
                "new": frappe.db.count(
                    "Alarm",
                    {"status": "New"}
                ),
                "acknowledged": frappe.db.count(
                    "Alarm",
                    {"status": "Acknowledged"}
                ),
                "in_progress": frappe.db.count(
                    "Alarm",
                    {"status": "In Progress"}
                ),
                "closed": frappe.db.count(
                    "Alarm",
                    {"status": "Closed"}
                ),
            },
            "incidents": frappe.db.count("Incident"),
        }
