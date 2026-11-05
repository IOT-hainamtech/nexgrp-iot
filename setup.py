"""Entry point kept for the existing after_migrate hook."""

from nextgrp.nextgrp_iot.service.mapping_setup_service import MAPPING_FIELDS, ensure_mapping_fields as _ensure_mapping_fields


def ensure_mapping_fields():
    _ensure_mapping_fields()
    from nextgrp.api.m1_schema_bootstrap import ensure_ml_fields
    ensure_ml_fields()
