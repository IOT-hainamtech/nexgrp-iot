
import json
import secrets

import frappe
from frappe.utils import add_to_date, now_datetime

from nextgrp.nextgrp_iot.service.integration_security import (
    is_data_plane,
    require_device_scope,
    require_command_scope,
)


class CommandRepository:

    def get_device_and_profile(self, device_id):
        if is_data_plane():
            require_device_scope(device_id)

        if not frappe.db.exists("Device", device_id):
            frappe.throw(
                "Device not found",
                frappe.DoesNotExistError,
            )

        device = frappe.get_doc(
            "Device", device_id
        )

        if not device.device_profile:
            frappe.throw(
                "Device Profile is required",
                frappe.ValidationError,
            )

        profile = frappe.get_doc(
            "Device Profile",
            device.device_profile,
        )

        if not is_data_plane():
            device.check_permission("read")
            profile.check_permission("read")

        return device, profile

    def create(self, values):
        # Data Plane cannot create commands.
        if is_data_plane():
            frappe.throw(
                "Data Plane cannot create commands",
                frappe.PermissionError,
            )

        doc = frappe.get_doc({
            "doctype": "Device Command",
            **values,
        })

        doc.insert()
        return doc

    def get_existing(self, command_id):
        if not frappe.db.exists(
            "Device Command", command_id
        ):
            return None

        doc = frappe.get_doc(
            "Device Command",
            command_id,
        )

        # This method is used by the manager-side
        # create_command idempotency check.
        doc.check_permission("read")

        return doc

    def list_for_device(self, device_id):
        # Command history is manager-facing only.
        # Keep Frappe's built-in read permission.
        return frappe.get_list(
            "Device Command",
            filters={"device": device_id},
            fields=[
                "command_id",
                "device",
                "command_code",
                "status",
                "requested_at",
                "requested_by",
                "expires_at",
                "attempt_count",
                "dispatched_at",
                "ack_at",
                "result_json",
            ],
            order_by="creation desc",
            limit_page_length=50,
        )

    def claim(self, device_id):
        # Defensive check at the persistence boundary.
        if is_data_plane():
            require_device_scope(device_id)

        # Serialize claim attempts for this Device.
        device_rows = frappe.db.sql(
            """
            SELECT name
            FROM `tabDevice`
            WHERE name = %s
            FOR UPDATE
            """,
            (device_id,),
            as_dict=True,
        )

        if not device_rows:
            frappe.throw(
                "Device not found",
                frappe.DoesNotExistError,
            )

        now = now_datetime()

        # A command can be claimed when it is:
        # - Pending, or
        # - Dispatched but the lease has expired.
        # Expired commands are not issued.
        rows = frappe.db.sql(
            """
            SELECT name
            FROM `tabDevice Command`
            WHERE device = %s
              AND expires_at > %s
              AND (
                    status = 'Pending'
                    OR (
                        status = 'Dispatched'
                        AND (
                            lease_until IS NULL
                            OR lease_until < %s
                        )
                    )
              )
            ORDER BY creation ASC
            LIMIT 1
            FOR UPDATE
            """,
            (device_id, now, now),
            as_dict=True,
        )

        if not rows:
            return {"command": None}

        doc = frappe.get_doc(
            "Device Command",
            rows[0].name,
        )

        if is_data_plane():
            require_device_scope(doc.device)
        else:
            doc.check_permission("write")

        doc.status = "Dispatched"
        doc.delivery_token = secrets.token_urlsafe(24)
        doc.lease_until = add_to_date(
            now,
            seconds=60,
        )
        doc.dispatched_at = now
        doc.attempt_count = (
            int(doc.attempt_count or 0) + 1
        )

        doc.save(
            ignore_permissions=is_data_plane()
        )

        return {
            "command": {
                "command_id": doc.command_id,
                "device_id": doc.device,
                "command_code": doc.command_code,
                "parameters": json.loads(
                    doc.parameters_json or "{}"
                ),
                "delivery_token": doc.delivery_token,
                "expires_at": str(doc.expires_at),
                "lease_until": str(doc.lease_until),
                "attempt_count": doc.attempt_count,
            }
        }

    def acknowledge(
        self,
        command_id,
        delivery_token,
        status,
        result_json,
    ):
        if is_data_plane():
            require_command_scope(command_id)

        rows = frappe.db.sql(
            """
            SELECT name
            FROM `tabDevice Command`
            WHERE command_id = %s
            FOR UPDATE
            """,
            (command_id,),
            as_dict=True,
        )

        if not rows:
            frappe.throw(
                "Command not found",
                frappe.DoesNotExistError,
            )

        doc = frappe.get_doc(
            "Device Command",
            rows[0].name,
        )

        if is_data_plane():
            require_device_scope(doc.device)
        else:
            doc.check_permission("write")

        if doc.delivery_token != delivery_token:
            frappe.throw(
                "Invalid or stale delivery token",
                frappe.PermissionError,
            )

        # Idempotent acknowledgement.
        if doc.status in ("Succeeded", "Failed"):
            if (
                doc.status == status
                and doc.result_json == result_json
            ):
                return {
                    "command_id": doc.command_id,
                    "status": doc.status,
                    "duplicate": True,
                }

            frappe.throw(
                "Conflicting command acknowledgement",
                frappe.ValidationError,
            )

        if doc.status != "Dispatched":
            frappe.throw(
                "Command was not dispatched",
                frappe.ValidationError,
            )

        if status not in ("Succeeded", "Failed"):
            frappe.throw(
                "Invalid acknowledgement status",
                frappe.ValidationError,
            )

        doc.status = status
        doc.ack_at = now_datetime()
        doc.result_json = result_json

        doc.save(
            ignore_permissions=is_data_plane()
        )

        return {
            "command_id": doc.command_id,
            "status": doc.status,
            "duplicate": False,
        }
