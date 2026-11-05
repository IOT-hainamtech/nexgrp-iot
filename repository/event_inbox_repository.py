
import json

import frappe
from frappe.utils import now_datetime

from nextgrp.nextgrp_iot.service.integration_security import (
    is_data_plane,
    require_device_scope,
)


class EventInboxRepository:

    def lock_device(self, device_id):
        if not isinstance(device_id, str) or not device_id:
            frappe.throw(
                "device_id is required",
                frappe.ValidationError,
            )

        if is_data_plane():
            require_device_scope(device_id)

        rows = frappe.db.sql(
            """
            SELECT name
            FROM `tabDevice`
            WHERE name = %s
            FOR UPDATE
            """,
            (device_id,),
            as_dict=True,
        )

        if not rows:
            frappe.throw(
                "Device not found",
                frappe.DoesNotExistError,
            )

    def get_event(self, event_id):
        if not isinstance(event_id, str) or not event_id:
            frappe.throw(
                "event_id is required",
                frappe.ValidationError,
            )

        rows = frappe.db.sql(
            """
            SELECT
                device,
                payload_hash,
                result_json
            FROM `tabIoT Event Inbox`
            WHERE event_id = %s
            FOR UPDATE
            """,
            (event_id,),
            as_dict=True,
        )

        if not rows:
            return None

        row = rows[0]
        if is_data_plane():
            require_device_scope(row.device)

        return row

    def get_episode_rule_version(self, alarm_key, device_id):
        """Read the version recorded by the first accepted occurrence.

        Keep episode metadata in the existing inbox result JSON so Rule
        version changes do not require an Alarm DocType schema migration.
        Older inbox rows without this value return None.
        """
        if is_data_plane():
            require_device_scope(device_id)

        rows = frappe.db.sql(
            """
            SELECT result_json
            FROM `tabIoT Event Inbox`
            WHERE alarm_key = %s
              AND device = %s
              AND event_kind = 'occurrence'
            ORDER BY processed_at ASC, creation ASC
            LIMIT 1
            """,
            (alarm_key, device_id),
            as_dict=True,
        )

        if not rows:
            return None

        try:
            result = json.loads(rows[0].result_json)
            version = result.get("rule_version")
            if type(version) is int and version > 0:
                return version
        except (TypeError, ValueError, AttributeError):
            pass

        return None

    def save_event(
        self,
        event_id,
        payload_hash,
        event_kind,
        device_id,
        alarm_key,
        source_event_id,
        result,
    ):
        if is_data_plane():
            require_device_scope(device_id)

        if event_kind not in ("occurrence", "cleared"):
            frappe.throw(
                "Invalid event kind",
                frappe.ValidationError,
            )

        if not isinstance(result, dict):
            frappe.throw(
                "Event result must be an object",
                frappe.ValidationError,
            )

        result_json = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
        )

        doc = frappe.get_doc({
            "doctype": "IoT Event Inbox",
            "event_id": event_id,
            "payload_hash": payload_hash,
            "event_kind": event_kind,
            "device": device_id,
            "alarm_key": alarm_key,
            "source_event_id": source_event_id,
            "result_json": result_json,
            "processed_at": now_datetime(),
        })

        doc.insert(
            ignore_permissions=is_data_plane()
        )

        return doc
