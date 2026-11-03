
import frappe


class DashboardRepository:

    def get_summary(self):
        return {
            "sites": frappe.db.count("Site"),
            "zones": frappe.db.count("Zone"),
            "devices": {
                "total": frappe.db.count("Device"),
                "online": frappe.db.count(
                    "Device",
                    {"online_state": "Online"}
                ),
                "offline": frappe.db.count(
                    "Device",
                    {"online_state": "Offline"}
                ),
                "unknown": frappe.db.count(
                    "Device",
                    {"online_state": "Unknown"}
                ),
            },
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
