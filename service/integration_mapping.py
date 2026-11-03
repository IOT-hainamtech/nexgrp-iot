"""Explicit read-side identities; local authorization always precedes mapping."""

from dataclasses import dataclass

import frappe


ORG_FIELD = "custom_data_plane_org_code"
SITE_FIELD = "custom_data_plane_site_code"
DEVICE_FIELD = "custom_data_plane_device_id"
RULE_FIELD = "custom_data_plane_rule_id"


@dataclass(frozen=True)
class DeviceReadMapping:
    local_device_id: str
    local_org_code: str
    local_site_code: str
    remote_device_id: str
    remote_org_code: str
    remote_site_code: str


def get_device_read_mapping(device, organization):
    """Resolve saved form values after the caller authorizes the local Device."""
    remote_device_id = device.get(DEVICE_FIELD)
    if not remote_device_id:
        return None

    site = frappe.db.get_value("Site", device.site, ["organization", SITE_FIELD], as_dict=True)
    if not site or not organization or site.organization != organization:
        raise ValueError("Device mapping scope mismatch")

    mapping = DeviceReadMapping(
        local_device_id=device.name,
        local_org_code=organization,
        local_site_code=device.site,
        remote_device_id=remote_device_id,
        remote_org_code=frappe.db.get_value("Organization", organization, ORG_FIELD),
        remote_site_code=site.get(SITE_FIELD),
    )
    if not all(isinstance(value, str) and value.strip() == value and value
               for value in vars(mapping).values()):
        raise ValueError("Invalid Device mapping")
    return mapping


def localize_read_response(body, mapping):
    """Translate identity fields, preserving samples, event IDs and timestamps."""
    identities = {
        "device_id": (mapping.remote_device_id, mapping.local_device_id),
        "org_code": (mapping.remote_org_code, mapping.local_org_code),
        "organization": (mapping.remote_org_code, mapping.local_org_code),
        "site_code": (mapping.remote_site_code, mapping.local_site_code),
    }

    def localize(row):
        if not isinstance(row, dict):
            return row
        result = dict(row)
        for field, (remote, local) in identities.items():
            if field in row:
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
            result[field] = [localize(row) for row in result[field]]
    return result
