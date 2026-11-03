"""Manager-only IF-03 synchronization of an existing mapped Rule."""

import frappe

from nextgrp.nextgrp_iot.service.integration_security import require_manager
from nextgrp.nextgrp_iot.service.rule_sync_service import RuleSyncService


@frappe.whitelist(methods=["POST"])
def sync_rule(rule_id=None):
    require_manager()
    return RuleSyncService().sync_rule(rule_id)
