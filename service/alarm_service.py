import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe
from frappe.realtime import get_doctype_room
from frappe.utils import get_datetime, get_system_timezone

from nextgrp.nextgrp_iot.repository.alarm_repository import AlarmRepository
from nextgrp.nextgrp_iot.repository.event_inbox_repository import EventInboxRepository


class AlarmService:
    def __init__(self):
        self.repository = AlarmRepository()
        self.inbox = EventInboxRepository()

    @staticmethod
    def require_text(data, key):
        value = data.get(key)

        if not isinstance(value, str) or not value.strip():
            frappe.throw(
                f"{key} is required",
                frappe.ValidationError,
            )

        value = value.strip()

        if len(value) > 140:
            frappe.throw(
                f"{key} exceeds 140 characters",
                frappe.ValidationError,
            )

        return value

    @staticmethod
    def parse_time(value, key):
        if not isinstance(value, str):
            frappe.throw(
                f"{key} must be an ISO datetime",
                frappe.ValidationError,
            )

        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            frappe.throw(
                f"Invalid {key}",
                frappe.ValidationError,
            )

        if dt.tzinfo is None or dt.utcoffset() is None:
            frappe.throw(
                f"{key} must contain a timezone",
                frappe.ValidationError,
            )

        return dt.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)

    def process(self, payload):
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                frappe.throw(
                    "Invalid JSON payload",
                    frappe.ValidationError,
                )

        if not isinstance(payload, dict):
            frappe.throw(
                "Payload must be an object",
                frappe.ValidationError,
            )

        event_id = self.require_text(payload, "event_id")
        org_code = self.require_text(payload, "org_code")
        alarm_key = self.require_text(payload, "alarm_key")
        episode_key = self.require_text(payload, "episode_key")
        kind = self.require_text(payload, "kind")
        rule_id = self.require_text(payload, "rule_id")
        device_id = self.require_text(payload, "device_id")

        if episode_key != alarm_key:
            frappe.throw(
                "episode_key must equal alarm_key",
                frappe.ValidationError,
            )

        if kind not in {"occurrence", "cleared"}:
            frappe.throw(
                "kind must be occurrence or cleared",
                frappe.ValidationError,
            )

        event = payload.get("event")

        if not isinstance(event, dict):
            frappe.throw(
                "event must be an object",
                frappe.ValidationError,
            )

        source_event_id = self.require_text(event, "event_id")
        event_type = self.require_text(event, "event_type")

        event_data = event.get("data") or {}

        if not isinstance(event_data, dict):
            frappe.throw(
                "event.data must be an object",
                frappe.ValidationError,
            )

        # Canonical hash: same JSON content -> same hash.
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

        payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        # Serialize processing for the same Device.
        self.inbox.lock_device(device_id)

        existing_event = self.inbox.get_event(event_id)

        if existing_event:
            if existing_event.payload_hash != payload_hash:
                frappe.throw(
                    "event_id reused with different payload",
                    frappe.ValidationError,
                )

            previous_result = json.loads(existing_event.result_json)

            # Replay the stored result without updating
            # Alarm or publishing another realtime event.
            previous_result["changed"] = False
            previous_result["duplicate"] = True
            previous_result["replayed"] = True

            return previous_result

        device = self.repository.get_device(device_id)
        rule = self.repository.get_rule(rule_id)

        # Đối chiếu Organization qua Device -> Site.
        site_org = frappe.db.get_value("Site", device.site, "organization")

        agency_code = (
            frappe.db.get_value("Organization", site_org, "agency_code")
            if site_org
            else None
        )

        if org_code not in {site_org, agency_code}:
            frappe.throw(
                "Device does not belong to org_code",
                frappe.ValidationError,
            )

        # M3.1 chỉ hỗ trợ Rule theo Device.
        if rule.scope_type != "Device" or rule.scope_ref != device.name:
            frappe.throw(
                "Rule scope does not match Device",
                frappe.ValidationError,
            )

        requested_version = None
        if payload.get("rule_version") is not None:
            try:
                requested_version = int(payload["rule_version"])
            except (TypeError, ValueError):
                frappe.throw(
                    "Invalid rule_version",
                    frappe.ValidationError,
                )
            if requested_version < 1:
                frappe.throw(
                    "Invalid rule_version",
                    frappe.ValidationError,
                )

        severity = rule.severity

        if payload.get("severity") and payload["severity"] != severity:
            frappe.throw(
                "Severity does not match Rule",
                frappe.ValidationError,
            )

        summary = event_data.get("summary")

        if not isinstance(summary, str) or not summary.strip():
            summary = f"{event_type} on {device_id}"

        summary = summary.strip()[:300]

        alarm = self.repository.get_alarm_for_update(alarm_key)

        if alarm:
            if alarm.device != device.name or alarm.rule != rule.name:
                frappe.throw(
                    "Alarm key belongs to another Rule/Device",
                    frappe.ValidationError,
                )

        # Pin each alarm episode to the version that created it. The version
        # is stored in the existing Event Inbox result_json, so this does not
        # require adding fields to the Alarm or Inbox DocTypes.
        episode_version = (
            self.inbox.get_episode_rule_version(alarm_key, device.name)
            if alarm
            else None
        )
        expected_version = episode_version or int(rule.version)
        if requested_version is not None and requested_version != expected_version:
            frappe.throw(
                "Rule version does not match Alarm episode",
                frappe.ValidationError,
            )
        event_rule_version = requested_version or expected_version

        changed = False
        duplicate = False

        if kind == "occurrence":
            if not rule.active and not alarm:
                frappe.throw(
                    "Rule is inactive",
                    frappe.ValidationError,
                )

            occurred_at = self.parse_time(
                payload.get("raised_at"),
                "raised_at",
            )

            try:
                seq = int(payload.get("occurrence_seq"))
            except (TypeError, ValueError):
                frappe.throw(
                    "Invalid occurrence_seq",
                    frappe.ValidationError,
                )

            if seq < 1:
                frappe.throw(
                    "occurrence_seq must be positive",
                    frappe.ValidationError,
                )

            if not alarm:
                if seq != 1:
                    frappe.throw(
                        "First occurrence_seq must be 1",
                        frappe.ValidationError,
                    )

                alarm = self.repository.create_alarm(
                    {
                        "alarm_key": alarm_key,
                        "rule": rule.name,
                        "device": device.name,
                        "site": device.site,
                        "zone": device.zone,
                        "severity": severity,
                        "status": "New",
                        "first_occurred": occurred_at,
                        "last_occurred": occurred_at,
                        "occurrence_count": 1,
                        "source_event_id": source_event_id,
                        "event_type": event_type,
                        "summary": summary,
                    }
                )

                changed = True

            else:
                current_seq = int(alarm.occurrence_count or 0)

                if seq <= current_seq:
                    frappe.throw(
                        "occurrence_seq already consumed by another event",
                        frappe.ValidationError,
                    )

                elif alarm.cleared_at:
                    frappe.throw(
                        "Alarm episode already cleared",
                        frappe.ValidationError,
                    )

                elif seq != current_seq + 1:
                    frappe.throw(
                        "Out-of-order occurrence_seq",
                        frappe.ValidationError,
                    )

                elif (
                    alarm.last_occurred
                    and occurred_at <= get_datetime(
                        alarm.last_occurred
                    )
                ):
                    frappe.throw(
                        "raised_at must increase",
                        frappe.ValidationError,
                    )

                else:
                    alarm.occurrence_count = seq
                    alarm.last_occurred = occurred_at
                    alarm.source_event_id = source_event_id
                    alarm.event_type = event_type
                    alarm.summary = summary

                    self.repository.save_alarm(alarm)
                    changed = True

        else:
            cleared_at = self.parse_time(
                payload.get("cleared_at"),
                "cleared_at",
            )

            if not alarm:
                frappe.throw(
                    "Cannot clear a missing Alarm",
                    frappe.ValidationError,
                )

            if alarm.cleared_at:
                frappe.throw(
                    "Alarm already cleared by another event",
                    frappe.ValidationError,
                )

            else:
                if (
                    alarm.last_occurred
                    and cleared_at < get_datetime(
                        alarm.last_occurred
                    )
                ):
                    frappe.throw(
                        "cleared_at precedes last occurrence",
                        frappe.ValidationError,
                    )

                alarm.cleared_at = cleared_at
                alarm.source_event_id = source_event_id

                if rule.auto_close:
                    alarm.status = "Closed"

                self.repository.save_alarm(alarm)
                changed = True

        result = {
            "alarm_key": alarm.name,
            "kind": kind,
            "status": alarm.status,
            "severity": alarm.severity,
            "device_id": device.name,
            "occurrence_count": int(alarm.occurrence_count or 0),
            "cleared_at": str(alarm.cleared_at) if alarm.cleared_at else None,
            "rule_version": event_rule_version,
            "changed": changed,
            "duplicate": duplicate,
        }

        if changed:
            frappe.publish_realtime(
                event="nextgrp_iot_alarm",
                message=result,
                room=get_doctype_room("Alarm"),
                after_commit=True,
            )

            self.inbox.save_event(
                event_id=event_id,
                payload_hash=payload_hash,
                event_kind=kind,
                device_id=device.name,
                alarm_key=alarm.name,
                source_event_id=source_event_id,
                result=result,
            )

        return result
