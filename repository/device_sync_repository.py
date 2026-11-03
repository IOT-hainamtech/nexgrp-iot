"""Internal reads for the trusted Device sync worker, not a public API."""

import frappe


class DeviceSyncRepository:
    def get_document(self, doctype, name):
        return frappe.get_doc(doctype, name)

    def get_device_rules(self, device_id):
       
        names = frappe.get_all(
            "Rule Configuration",
            filters={"scope_type": "Device", "scope_ref": device_id},
            pluck="name",
            order_by="name asc",
        )
        return [self.get_document("Rule Configuration", name) for name in names]
