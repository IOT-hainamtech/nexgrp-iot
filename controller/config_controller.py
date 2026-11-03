
import frappe

from nextgrp.nextgrp_iot.service.config_service import (
    ConfigService,
)
from nextgrp.nextgrp_iot.service.integration_security import (
    is_data_plane,
    require_device_scope,
    require_manager_or_data_plane,
)


@frappe.whitelist(methods=["GET"])
def get_device_config(device_id=None):
    require_manager_or_data_plane()

    if is_data_plane():
        require_device_scope(device_id)

    return ConfigService().get_device_config(device_id)
