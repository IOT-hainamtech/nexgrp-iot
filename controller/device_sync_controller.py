"""Device lifecycle hooks; network requests run only in the background job."""

from nextgrp.nextgrp_iot.service.device_sync_service import DeviceSyncService


def validate_device(doc, method=None):
    DeviceSyncService().validate_device(doc)


def device_saved(doc, method=None):
    DeviceSyncService().device_saved(doc)
