
import frappe

from nextgrp.nextgrp_iot.service.status_service import (
    StatusService,
)
from nextgrp.nextgrp_iot.service.integration_security import (
    is_data_plane,
    require_device_scope,
    require_manager_or_data_plane,
)


@frappe.whitelist(methods=["POST"])
def receive_status(device_id=None, state=None):
    require_manager_or_data_plane()

    if is_data_plane():
        require_device_scope(device_id)

    return StatusService().update_status(device_id,state)
