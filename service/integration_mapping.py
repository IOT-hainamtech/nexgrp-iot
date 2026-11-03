"""Resolve local and Data Plane identities.

Callers must authorize access before resolving mappings.
This module does not create records or send network requests.
"""

from dataclasses import dataclass

import frappe


ORG_FIELD = "custom_data_plane_org_code"
SITE_FIELD = "custom_data_plane_site_code"
DEVICE_FIELD = "custom_data_plane_device_id"
RULE_FIELD = "custom_data_plane_rule_id"


@dataclass(frozen=True)
class DeviceReadMapping:
    """Keep the existing public type compatible with current callers."""

    local_device_id: str
    local_org_code: str
    local_site_code: str
    remote_device_id: str
    remote_org_code: str
    remote_site_code: str


def _require_identity(value, label):
    """Validate identities without silently modifying them."""

    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is required")

    if value != value.strip():
        raise ValueError(f"{label} contains surrounding whitespace")

    if len(value) > 140:
        raise ValueError(f"{label} exceeds 140 characters")

    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} contains control characters")

    return value


def _remote_identity(configured_value, local_value, label):
    """Prefer an explicit alias; otherwise use the local identity."""

    if configured_value is None or configured_value == "":
        configured_value = local_value

    return _require_identity(configured_value, label)


def get_rule_remote_id(rule):
    """Resolve a Rule alias without changing its version or conditions."""

    local_rule_id = rule.get("rule_id") or rule.name

    return _remote_identity(
        rule.get(RULE_FIELD),
        local_rule_id,
        "Data Plane Rule ID",
    )


def get_device_read_mapping(device, organization):
    """Resolve Device, Site and Organization identities independently.

    A missing Device alias must not bypass parent mappings.
    The caller must already have authorized access to the local Device.
    """

    local_device_id = _require_identity(
        device.name,
        "Local Device ID",
    )
    local_site_id = _require_identity(
        device.get("site"),
        "Local Site ID",
    )
    local_org_id = _require_identity(
        organization,
        "Local Organization ID",
    )

    site = frappe.db.get_value(
        "Site",
        local_site_id,
        ["name", "organization", SITE_FIELD],
        as_dict=True,
    )

    if (
        not site
        or site.get("name") != local_site_id
        or site.get("organization") != local_org_id
    ):
        raise ValueError("Device mapping scope mismatch")

    org = frappe.db.get_value(
        "Organization",
        local_org_id,
        ["name", ORG_FIELD],
        as_dict=True,
    )

    if not org or org.get("name") != local_org_id:
        raise ValueError("Organization mapping not found")

    device_fallback = device.get("device_id") or local_device_id

    return DeviceReadMapping(
        local_device_id=local_device_id,
        local_org_code=local_org_id,
        local_site_code=local_site_id,
        remote_device_id=_remote_identity(
            device.get(DEVICE_FIELD),
            device_fallback,
            "Data Plane Device ID",
        ),
        remote_org_code=_remote_identity(
            org.get(ORG_FIELD),
            local_org_id,
            "Data Plane Organization ID",
        ),
        remote_site_code=_remote_identity(
            site.get(SITE_FIELD),
            local_site_id,
            "Data Plane Site ID",
        ),
    )


def localize_read_response(body, mapping):
    """Translate identities while preserving measurements and events."""

    identities = {
        "device_id": (
            mapping.remote_device_id,
            mapping.local_device_id,
        ),
        "org_code": (
            mapping.remote_org_code,
            mapping.local_org_code,
        ),
        "organization": (
            mapping.remote_org_code,
            mapping.local_org_code,
        ),
        "site_code": (
            mapping.remote_site_code,
            mapping.local_site_code,
        ),
    }

    def localize(row):
        if not isinstance(row, dict):
            return row

        result = dict(row)

        for field, (remote, local) in identities.items():
            if field not in row:
                continue

            if row[field] != remote:
                raise ValueError("Data Plane response identity mismatch")

            result[field] = local

        return result

    result = localize(body)

    for field in ("latest_snapshot", "latest_event"):
        if field in result:
            result[field] = localize(result[field])

    for field in ("events", "points"):
        if isinstance(result.get(field), list):
            result[field] = [
                localize(row)
                for row in result[field]
            ]

    return result