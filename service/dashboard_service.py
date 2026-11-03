
from frappe.utils import now_datetime

from nextgrp.nextgrp_iot.repository.dashboard_repository import (
    DashboardRepository,
)


class DashboardService:

    def __init__(self):
        self.repository = DashboardRepository()

    def get_summary(self):
        summary = self.repository.get_summary()

        return {
            "summary": summary,
            "updated_at": str(now_datetime()),
        }
