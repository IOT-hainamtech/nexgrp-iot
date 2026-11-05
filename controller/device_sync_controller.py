"""Device lifecycle hooks; network requests run only in the background job."""

from nextgrp.nextgrp_iot.service.device_sync_service import DeviceSyncService


def validate_device(doc, method=None):
    DeviceSyncService().validate_device(doc)
    from nextgrp.nextgrp_iot.service.ml_registry_service import validate_ml_device
    validate_ml_device(doc, method)


def device_saved(doc, method=None):
    DeviceSyncService().device_saved(doc)
    from nextgrp.nextgrp_iot.service.ml_registry_service import device_saved as ml_device_saved
    ml_device_saved(doc, method)
