"""Organization-scoped, read-only discovery for authenticated ML workers."""
import frappe

from nextgrp.nextgrp_iot.service.ml_registry_service import list_ml_devices as _list


@frappe.whitelist(methods=['GET'])
def list_ml_devices(org_code=None, after='', page_size=100):
    return _list(org_code, after, page_size)


@frappe.whitelist(methods=['GET'])
def wait_ml_events(org_code=None, cursor='$', timeout=10):
    from nextgrp.nextgrp_iot.service.ml_event_service import wait_ml_events as _wait
    return _wait(org_code, cursor, timeout)
