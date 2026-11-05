
import frappe

from nextgrp.nextgrp_iot.repository.config_repository import (
    ConfigRepository,
)
from nextgrp.nextgrp_iot.service.integration_mapping import (
    get_device_read_mapping,
    get_rule_remote_id,
)


DEVICE_FIELDS = (
    "device_id", "device_profile", "site", "zone",
    "status", "connectivity_type", "config_version",
    "online_state", "last_seen", "fw_version",
    "manufacturer", "model",
)

SITE_FIELDS = (
    "site_code", "site_name", "organization",
    "site_status", "timezone",
)

ZONE_FIELDS = (
    "zone_code", "zone_name", "site",
    "parent_zone", "zone_type",
)

PROFILE_FIELDS = (
    "profile_code", "profile_name", "version",
    "protocol", "device_class", "signal_model",
    "expected_interval_s", "heartbeat_interval_s",
    "offline_after_missed", "supports_telemetry",
    "supports_events", "supports_commands",
)


def pick(doc, fields):
    return {field: doc.get(field) for field in fields}


class ConfigService:

    def __init__(self):
        self.repo = ConfigRepository()

    def get_device_config(self, device_id):
        if not isinstance(device_id, str) or not device_id.strip():
            frappe.throw("device_id is required", frappe.ValidationError)

        device = self.repo.get_document(
            "Device", device_id.strip()
        )

        profile = self.repo.get_document(
            "Device Profile", device.device_profile
        )

        site = self.repo.get_document(
            "Site", device.site
        )

        zone = None

        if device.zone:
            zone = self.repo.get_document(
                "Zone", device.zone
            )

            if zone.site != device.site:
                frappe.throw(
                    "Device Zone does not belong to Site",
                    frappe.ValidationError,
                )

        device_mapping = get_device_read_mapping(device, site.organization)
        rules = []

        for rule in self.repo.get_rules():
            scope = rule.scope_type
            matched = False

            if scope == "Device":
                matched = rule.scope_ref == device.name

            elif scope == "Device Profile":
                matched = (
                    rule.scope_profile == profile.name
                )

            elif scope == "Zone":
                matched = bool(
                    device.zone and
                    rule.scope_zone == device.zone
                )

            elif scope == "Site":
                matched = rule.scope_site == device.site

            # Asset / Device Group require a separate
            # membership model. Do not match them blindly.

            if matched:
                rule_data = dict(rule)
                rule_data["external_id"] = get_rule_remote_id(rule)
                rules.append(rule_data)

        device_data = pick(device, DEVICE_FIELDS)
        profile_data = {
            **pick(profile, PROFILE_FIELDS),
            # Device Sync currently uses profile.name as the Data Plane code.
            "external_id": profile.name,
        }
        site_data = pick(site, SITE_FIELDS)
        device_data["external_id"] = device_mapping.remote_device_id
        site_data["external_id"] = device_mapping.remote_site_code

        return {
            "device": device_data,
            "site": site_data,
            "identifiers": {
                "organization": {
                    "nextgrp_id": site.organization,
                    "external_id": device_mapping.remote_org_code,
                },
                "site": {
                    "nextgrp_id": site.name,
                    "external_id": device_mapping.remote_site_code,
                },
                "device": {
                    "nextgrp_id": device.name,
                    "external_id": device_mapping.remote_device_id,
                },
                "profile": {
                    "nextgrp_id": profile.name,
                    "external_id": profile.name,
                },
                "rules": [
                    {
                        "nextgrp_id": rule.get("name"),
                        "external_id": rule.get("external_id"),
                    }
                    for rule in rules
                ],
            },
            "zone": (
                pick(zone, ZONE_FIELDS)
                if zone else None
            ),
            "profile": {
                **profile_data,
                "data_points": [
                    pick(row, (
                        "code", "label", "data_type",
                        "unit", "min", "max",
                        "decimal_places", "is_required",
                        "channel", "allowed_values",
                    ))
                    for row in profile.profile_data_points
                ],
                "event_types": [
                    pick(row, (
                        "event_code", "event_name",
                        "payload_schema",
                        "default_severity",
                        "requires_evidence",
                    ))
                    for row in profile.event_types
                ],
                "commands": [
                    pick(row, (
                        "command_code", "command_name",
                        "parameter_schema",
                        "risk_level",
                        "requires_approval",
                        "description",
                    ))
                    for row in profile.commands
                ],
            },
            "rules": rules,
        }
