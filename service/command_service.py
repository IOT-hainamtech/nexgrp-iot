
import json
import re
from uuid import uuid4

import frappe
from frappe.utils import add_to_date, now_datetime

from nextgrp.nextgrp_iot.repository.command_repository import (
    CommandRepository,
)


def require_text(value, field):
    if not isinstance(value, str) or not value.strip():
        frappe.throw(
            f"{field} is required",
            frappe.ValidationError,
        )
    return value.strip()


def parse_json_object(value, field):
    if value is None or value == "":
        return {}

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            frappe.throw(
                f"{field} must be valid JSON",
                frappe.ValidationError,
            )

    if not isinstance(value, dict):
        frappe.throw(
            f"{field} must be a JSON object",
            frappe.ValidationError,
        )

    return value


def canonical_json(value):
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        frappe.throw(
            "Invalid JSON values",
            frappe.ValidationError,
        )


def validate_parameters(params, schema_text):
    # No parameter schema means no parameters allowed.
    if not schema_text:
        if params:
            frappe.throw(
                "Command does not accept parameters",
                frappe.ValidationError,
            )
        return

    schema = parse_json_object(
        schema_text, "parameter_schema"
    )

    allowed_schema_keys = {
        "type",
        "properties",
        "required",
        "additionalProperties",
    }

    if (
        schema.get("type") != "object"
        or set(schema) - allowed_schema_keys
        or schema.get("additionalProperties", False)
        is not False
    ):
        frappe.throw(
            "Unsupported parameter schema",
            frappe.ValidationError,
        )

    properties = schema.get("properties", {})
    required = schema.get("required", [])

    if (
        not isinstance(properties, dict)
        or not isinstance(required, list)
        or not all(
            isinstance(key, str) for key in required
        )
        or any(
            key not in properties for key in required
        )
    ):
        frappe.throw(
            "Invalid parameter schema",
            frappe.ValidationError,
        )

    for key in params:
        if key not in properties:
            frappe.throw(
                f"Unexpected parameter: {key}",
                frappe.ValidationError,
            )

    for key in required:
        if key not in params:
            frappe.throw(
                f"Missing required parameter: {key}",
                frappe.ValidationError,
            )

    for key, value in params.items():
        spec = properties[key]

        if not isinstance(spec, dict):
            frappe.throw(
                f"Invalid schema for {key}",
                frappe.ValidationError,
            )

        if set(spec) - {
            "type", "enum", "minimum", "maximum"
        }:
            frappe.throw(
                f"Unsupported schema for {key}",
                frappe.ValidationError,
            )

        typ = spec.get("type")

        valid = {
            "string": isinstance(value, str),
            "integer": type(value) is int,
            "number": type(value) in (int, float),
            "boolean": type(value) is bool,
        }.get(typ, False)

        if not valid:
            frappe.throw(
                f"Invalid type for {key}",
                frappe.ValidationError,
            )

        if "enum" in spec:
            if (
                not isinstance(spec["enum"], list)
                or value not in spec["enum"]
            ):
                frappe.throw(
                    f"Invalid value for {key}",
                    frappe.ValidationError,
                )

        if typ in ("integer", "number"):
            for bound in ("minimum", "maximum"):
                if bound in spec and type(
                    spec[bound]
                ) not in (int, float):
                    frappe.throw(
                        f"Invalid {bound} for {key}",
                        frappe.ValidationError,
                    )

            if (
                "minimum" in spec
                and value < spec["minimum"]
            ):
                frappe.throw(
                    f"{key} is below minimum",
                    frappe.ValidationError,
                )

            if (
                "maximum" in spec
                and value > spec["maximum"]
            ):
                frappe.throw(
                    f"{key} exceeds maximum",
                    frappe.ValidationError,
                )

        elif "minimum" in spec or "maximum" in spec:
            frappe.throw(
                f"Unsupported bounds for {key}",
                frappe.ValidationError,
            )


class CommandService:
    def __init__(self):
        self.repo = CommandRepository()

    def create_command(
        self,
        device_id,
        command_code,
        parameters_json=None,
        command_id=None,
    ):
        device_id = require_text(
            device_id, "device_id"
        )
        command_code = require_text(
            command_code, "command_code"
        )

        params = parse_json_object(
            parameters_json, "parameters_json"
        )
        params_json = canonical_json(params)

        if len(params_json) > 16000:
            frappe.throw(
                "Command parameters too large",
                frappe.ValidationError,
            )

        device, profile = (
            self.repo.get_device_and_profile(device_id)
        )

        if device.status != "Active":
            frappe.throw(
                "Device is not Active",
                frappe.ValidationError,
            )

        if not profile.supports_commands:
            frappe.throw(
                "Device Profile does not support commands",
                frappe.ValidationError,
            )

        definition = next(
            (
                row for row in profile.commands
                if row.command_code == command_code
            ),
            None,
        )

        if definition is None:
            frappe.throw(
                "Command not declared in Device Profile",
                frappe.ValidationError,
            )

        if (
            definition.risk_level != "Low"
            or bool(definition.requires_approval)
        ):
            frappe.throw(
                "Command requires approval or is not low risk",
                frappe.PermissionError,
            )

        validate_parameters(
            params, definition.parameter_schema
        )

        if command_id is not None:
            command_id = require_text(
                command_id, "command_id"
            )

            if not re.fullmatch(
                r"CMD-[A-Za-z0-9_-]{8,96}",
                command_id,
            ):
                frappe.throw(
                    "Invalid command_id format",
                    frappe.ValidationError,
                )
        else:
            command_id = f"CMD-{uuid4().hex}"

        existing = self.repo.get_existing(command_id)

        if existing:
            if (
                existing.device != device.name
                or existing.command_code != command_code
                or existing.parameters_json != params_json
                or existing.requested_by
                != frappe.session.user
            ):
                frappe.throw(
                    "command_id reused with different request",
                    frappe.ValidationError,
                )

            return {
                "command_id": existing.command_id,
                "device_id": existing.device,
                "command_code": existing.command_code,
                "status": existing.status,
                "duplicate": True,
            }

        now = now_datetime()

        doc = self.repo.create({
            "command_id": command_id,
            "device": device.name,
            "command_code": command_code,
            "parameters_json": params_json,
            "status": "Pending",
            "requested_by": frappe.session.user,
            "requested_at": now,
            "expires_at": add_to_date(
                now, minutes=10
            ),
            "attempt_count": 0,
        })

        return {
            "command_id": doc.command_id,
            "device_id": doc.device,
            "command_code": doc.command_code,
            "status": doc.status,
            "duplicate": False,
        }

    def claim_command(self, device_id):
        device_id = require_text(
            device_id, "device_id"
        )

        device, _ = self.repo.get_device_and_profile(
            device_id
        )

        if device.status != "Active":
            frappe.throw(
                "Device is not Active",
                frappe.ValidationError,
            )

        return self.repo.claim(device_id)

    def ack_command(
        self,
        command_id,
        delivery_token,
        status,
        result_json=None,
    ):
        command_id = require_text(
            command_id, "command_id"
        )
        delivery_token = require_text(
            delivery_token, "delivery_token"
        )

        if status not in ("Succeeded", "Failed"):
            frappe.throw(
                "Invalid acknowledgement status",
                frappe.ValidationError,
            )

        result = parse_json_object(
            result_json, "result_json"
        )
        result_text = canonical_json(result)

        if len(result_text) > 16000:
            frappe.throw(
                "Command result too large",
                frappe.ValidationError,
            )

        return self.repo.acknowledge(
            command_id=command_id,
            delivery_token=delivery_token,
            status=status,
            result_json=result_text,
        )

    def list_commands(self, device_id):
        device_id = require_text(
            device_id, "device_id"
        )

        self.repo.get_device_and_profile(device_id)

        return {
            "data": self.repo.list_for_device(
                device_id
            )
        }
