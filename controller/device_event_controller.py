"""Receive Data Plane events and relay them to subscribed Dashboard clients."""

import frappe

from nextgrp.nextgrp_iot.service.device_event_service import DeviceEventService


@frappe.whitelist(methods=["POST"])
def receive_device_event(
    device_id=None,
    event_type=None,
    event_at=None,
    event_id=None,
    door_state=None,
    metrics=None,
    data_status=None,
):
    return DeviceEventService().receive(
        device_id=device_id,
        event_type=event_type,
        event_at=event_at,
        event_id=event_id,
        door_state=door_state,
        metrics=metrics,
        data_status=data_status,
    )
