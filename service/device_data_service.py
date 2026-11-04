"""Read Data Plane with server credentials after checking Device permissions."""

from urllib.parse import quote, urlsplit

import frappe
import requests

from nextgrp.nextgrp_iot.service.integration_mapping import (
    get_device_read_mapping,
    localize_read_response,
)


class DataPlaneReadError(frappe.ValidationError):
    http_status_code = 502


class DeviceDataService:
    def get(self, device_id, resource, limit=50, offset=0, cursor=None):
        if resource not in ("latest", "events", "telemetry", "status"):
            frappe.throw("Unsupported Device data resource", frappe.ValidationError)
        if not isinstance(device_id, str) or not device_id.strip():
            frappe.throw("device_id is required", frappe.ValidationError)

        device = frappe.get_doc("Device", device_id.strip())
        device.check_permission("read")
        org = frappe.db.get_value("Site", device.site, "organization")
        configured_org = frappe.conf.get("nextwireless_dp_org_code")
        if not org or org != configured_org:
            frappe.throw("Device is outside the configured Data Plane Organization", frappe.PermissionError)

        try:
            mapping = get_device_read_mapping(device, org)
        except (OSError, ValueError, KeyError, TypeError):
            raise DataPlaneReadError("Data Plane Device mapping missing or invalid") from None

        params = {}
        if resource in ("events", "telemetry"):
            try:
                limit, offset = int(str(limit)), int(str(offset))
            except (TypeError, ValueError):
                frappe.throw("Invalid data pagination", frappe.ValidationError)
            maximum = 1000 if resource == "telemetry" else 100
            if not 1 <= limit <= maximum or offset < 0:
                frappe.throw("Invalid data pagination", frappe.ValidationError)
            params = {"limit": limit}
            if resource == "events":
                params["offset"] = offset
            elif cursor is not None:
                if not isinstance(cursor, str) or not cursor or len(cursor) > 2048 or any(ord(c) < 32 for c in cursor):
                    frappe.throw("Invalid telemetry cursor", frappe.ValidationError)
                params["cursor"] = cursor

        base = str(frappe.conf.get("nextwireless_dp_base_url") or "").rstrip("/")
        token = frappe.conf.get("nextwireless_dp_inbound_token")
        parsed = urlsplit(base)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or not isinstance(token, str) or not token.strip()
        ):
            frappe.throw("Data Plane read configuration missing or invalid", DataPlaneReadError)

        external_id = mapping.remote_device_id if mapping else (device.device_id or device.name)
        external_org = mapping.remote_org_code if mapping else org
        path = f"/api/v1/devices/{quote(external_id, safe='')}/{resource}"
        headers = {
            "Authorization": f"Bearer {token.strip()}",
            "X-Org-Code": external_org,
            "Accept": "application/json",
            "ngrok-skip-browser-warning": "true",
        }
        body = self._fetch(base + path, headers, params, external_id, resource)
        if mapping:
            try:
                body = localize_read_response(body, mapping)
            except ValueError:
                raise DataPlaneReadError("Data Plane response identity mismatch") from None
        # Offline/STALE snapshots and historical events remain useful data.
        return body

    def _fetch(self, url, headers, params, external_id, resource):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=(3, 5) if resource == "status" else (5, 15),
                allow_redirects=False,
            )
        except requests.RequestException:
            # Do not expose transport errors, URLs or credential-bearing headers.
            raise DataPlaneReadError("Data Plane unavailable; please retry") from None
        if not 200 <= response.status_code < 300:
            raise DataPlaneReadError(f"Data Plane {resource} returned HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise DataPlaneReadError("Data Plane returned invalid JSON") from None
        if not isinstance(body, dict) or body.get("device_id") != external_id:
            raise DataPlaneReadError("Data Plane response Device ID mismatch")
        if resource == "status":
            state = body.get("current_state")
            if not isinstance(state, str) or state.strip().lower() not in ("online", "offline", "unknown"):
                raise DataPlaneReadError("Data Plane status returned invalid current_state")
            body["current_state"] = state.strip().lower()
        return body
