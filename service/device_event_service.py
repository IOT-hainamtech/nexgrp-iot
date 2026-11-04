"""Validate ephemeral Data Plane events and publish them over Frappe Socket.IO.

Data Plane remains the source of truth. This service does not persist telemetry
or event history in nextGRP.
"""

from datetime import datetime
import math
import re

import frappe
from frappe.realtime import get_doc_room

from nextgrp.nextgrp_iot.service.integration_mapping import get_device_read_mapping
from nextgrp.nextgrp_iot.service.integration_security import (
    is_data_plane,
    require_data_plane,
    require_device_scope,
    require_manager_or_data_plane,
)


EVENT_TYPES = {"DOOR_OPENED", "DOOR_CLOSED", "TELEMETRY_UPDATED"}
METRIC_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
DATA_STATUSES = {"LIVE", "STALE", "UNKNOWN"}


class DeviceEventService:
    def receive(
        self,
        *,
        device_id,
        event_type,
        event_at,
        event_id=None,
        door_state=None,
        metrics=None,
        data_status=None,
    ):
        require_manager_or_data_plane()

        device_id = self._required_string(device_id, "device_id", 140)
        event_type = self._required_string(event_type, "event_type", 40).upper()
        if event_type not in EVENT_TYPES:
            frappe.throw("Unsupported Device event_type", frappe.ValidationError)

        event_at = self._validated_timestamp(event_at)
        event_id = self._optional_string(event_id, "event_id", 140)
        door_state, metrics = self._validated_data(event_type, door_state, metrics)
        data_status = self._validated_data_status(data_status)

        data_plane_request = is_data_plane()
        if data_plane_request:
            require_data_plane()

        if data_plane_request:
            require_device_scope(device_id)

        device = frappe.get_doc("Device", device_id)
        if not is_data_plane():
            device.check_permission("read")

        organization = frappe.db.get_value("Site", device.site, "organization")
        try:
            mapping = get_device_read_mapping(device, organization)
        except (OSError, ValueError, KeyError, TypeError):
            frappe.throw("Device integration mapping is missing or invalid", frappe.ValidationError)

        if device_id != mapping.local_device_id:
            frappe.throw("Device ID does not match its integration mapping", frappe.PermissionError)

        request = getattr(frappe.local, "request", None)
        remote_org = request.headers.get("X-Org-Code", "").strip() if request else ""
        if data_plane_request and (not remote_org or remote_org != mapping.remote_org_code):
            frappe.throw("Device is outside the supplied Data Plane Organization", frappe.PermissionError)

        payload = {
            "device_id": mapping.local_device_id,
            "event_id": event_id or f"{event_type}:{event_at}",
            "event_type": event_type,
            "event_at": event_at,
            "door_state": door_state,
            "metrics": metrics,
            "data_status": data_status,
        }
        frappe.publish_realtime(
            event="nextgrp_iot_device_event",
            message=payload,
            room=get_doc_room("Device", device.name),
            after_commit=True,
        )

        return {
            "accepted": True,
            "device_id": mapping.local_device_id,
            "data_plane_device_id": mapping.remote_device_id,
            "event_id": payload["event_id"],
            "event_type": event_type,
        }

    @staticmethod
    def _required_string(value, label, maximum):
        if not isinstance(value, str) or not value or value != value.strip():
            frappe.throw(f"{label} is required", frappe.ValidationError)
        if len(value) > maximum or any(ord(char) < 32 or ord(char) == 127 for char in value):
            frappe.throw(f"{label} is invalid", frappe.ValidationError)
        return value

    @classmethod
    def _optional_string(cls, value, label, maximum):
        if value in (None, ""):
            return None
        return cls._required_string(value, label, maximum)

    @staticmethod
    def _validated_timestamp(value):
        value = DeviceEventService._required_string(value, "event_at", 64)
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            frappe.throw("event_at must be an ISO-8601 timestamp", frappe.ValidationError)
        if parsed.tzinfo is None:
            frappe.throw("event_at must include a timezone", frappe.ValidationError)
        return value

    @staticmethod
    def _validated_data(event_type, door_state, metrics):
        if event_type in {"DOOR_OPENED", "DOOR_CLOSED"}:
            expected_state = "open" if event_type == "DOOR_OPENED" else "closed"
            if door_state is not None and str(door_state).strip().lower() != expected_state:
                frappe.throw("door_state conflicts with event_type", frappe.ValidationError)
            door_state = expected_state
        elif door_state is not None:
            frappe.throw("door_state is only valid for door events", frappe.ValidationError)

        if metrics is None:
            metrics = []
        if not isinstance(metrics, list) or len(metrics) > 50:
            frappe.throw("metrics must be an array of at most 50 values", frappe.ValidationError)

        normalized = []
        for item in metrics:
            if not isinstance(item, dict):
                frappe.throw("Each metric must be an object", frappe.ValidationError)
            code = item.get("code")
            value = item.get("value")
            unit = item.get("unit")
            label = item.get("label")
            if not isinstance(code, str) or not METRIC_CODE.fullmatch(code):
                frappe.throw("Metric code is invalid", frappe.ValidationError)
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                frappe.throw("Metric value must be a finite number or string", frappe.ValidationError)
            if isinstance(value, float) and not math.isfinite(value):
                frappe.throw("Metric value must be finite", frappe.ValidationError)
            if isinstance(value, str) and len(value) > 128:
                frappe.throw("Metric value is too long", frappe.ValidationError)
            row = {"code": code, "value": value}
            for key, text in (("unit", unit), ("label", label)):
                if text is not None:
                    if not isinstance(text, str) or len(text) > 80 or any(ord(char) < 32 for char in text):
                        frappe.throw(f"Metric {key} is invalid", frappe.ValidationError)
                    row[key] = text
            normalized.append(row)

        if event_type == "TELEMETRY_UPDATED" and not normalized:
            frappe.throw("TELEMETRY_UPDATED requires at least one metric", frappe.ValidationError)
        return door_state, normalized

    @staticmethod
    def _validated_data_status(value):
        if value is None or value == "":
            return None
        if not isinstance(value, str) or value.strip().upper() not in DATA_STATUSES:
            frappe.throw("data_status must be LIVE, STALE or UNKNOWN", frappe.ValidationError)
        return value.strip().upper()
