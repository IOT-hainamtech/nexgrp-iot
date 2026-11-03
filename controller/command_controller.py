
import frappe

from nextgrp.nextgrp_iot.service.command_service import (
    CommandService,
)
from nextgrp.nextgrp_iot.service.integration_security import (
    require_manager,
    require_data_plane,
    require_device_scope,
    require_command_scope,
)


@frappe.whitelist(methods=["POST"])
def create_command(
    device_id=None,
    command_code=None,
    parameters_json=None,
    command_id=None,
):
    require_manager()

    return CommandService().create_command(
        device_id=device_id,
        command_code=command_code,
        parameters_json=parameters_json,
        command_id=command_id,
    )


@frappe.whitelist(methods=["GET"])
def list_commands(device_id=None):
    require_manager()

    return CommandService().list_commands(device_id)


@frappe.whitelist(methods=["POST"])
def claim_command(device_id=None):
    require_data_plane()
    require_device_scope(device_id)

    return CommandService().claim_command(
        device_id
    )


@frappe.whitelist(methods=["POST"])
def ack_command(
    command_id=None,
    delivery_token=None,
    status=None,
    result_json=None,
):
    require_data_plane()
    require_command_scope(command_id)

    return CommandService().ack_command(
        command_id=command_id,
        delivery_token=delivery_token,
        status=status,
        result_json=result_json,
    )

