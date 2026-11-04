"""Authenticated, same-origin reads for the monitoring frontend."""

import frappe

from nextgrp.nextgrp_iot.service.device_data_service import DeviceDataService


@frappe.whitelist(methods=["GET"])
def get_status(device_id=None):
    return DeviceDataService().get(device_id, "status")


@frappe.whitelist(methods=["GET"])
def get_latest(device_id=None):
    return DeviceDataService().get(device_id, "latest")


@frappe.whitelist(methods=["GET"])
def get_events(device_id=None, limit=50, offset=0):
    return DeviceDataService().get(device_id, "events", limit=limit, offset=offset)


@frappe.whitelist(methods=["GET"])
def get_telemetry(device_id=None, limit=100, cursor=None):
    return DeviceDataService().get(device_id, "telemetry", limit=limit, cursor=cursor)
