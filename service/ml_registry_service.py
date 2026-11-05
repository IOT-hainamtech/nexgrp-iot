"""Opt-in ML inventory. No device IDs, profile names or credentials embedded."""
import hashlib

import frappe

from nextgrp.nextgrp_iot.service.integration_mapping import get_device_read_mapping
from nextgrp.nextgrp_iot.service.integration_security import (
    is_data_plane, require_manager, require_manager_or_data_plane,
)

ML_FIELD = 'custom_ml_enabled'
EVENT = 'AI_ANOMALY_DETECTED'


def profile_task(profile):
    points = {row.get('code'): row for row in profile.get('profile_data_points', [])}
    events = {row.get('event_code') for row in profile.get('event_types', [])}
    thermal = (bool(profile.get('supports_telemetry'))
               and points.get('temperature', {}).get('unit') in ('Cel', '°C')
               and points.get('humidity', {}).get('unit') in ('%RH', '%')
               and all(points[key].get('data_type') in ('Float', 'Int', 'Integer')
                       for key in ('temperature', 'humidity')))
    door = bool(profile.get('supports_events')) and {'DOOR_OPENED', 'DOOR_CLOSED'} <= events
    if not door or thermal:
        raise ValueError('ML hiện chỉ hỗ trợ cảm biến cửa S6. Nhiệt độ/độ ẩm S1 sử dụng cảnh báo theo quy tắc.')
    return 's6_anomaly'


def rule_identity(task, device):
    if task != 's6_anomaly':
        raise ValueError('ML hiện chỉ hỗ trợ cảm biến cửa S6.')
    sensor = 'S6'
    return f'ML-LIVE-{sensor}-'+hashlib.sha256(device.encode()).hexdigest()[:10]


def ml_rule(device, task):
    name = rule_identity(task, device.name)
    if not frappe.db.exists('Rule Configuration', name):
        raise ValueError('Chưa có Rule ML riêng; quản trị viên cần lưu lại Device đang bật ML.')
    rule = frappe.get_doc('Rule Configuration', name)
    if (rule.scope_type != 'Device' or rule.scope_ref != device.name
            or rule.event_type != EVENT or not rule.active):
        raise ValueError('Rule ML chưa hoạt động hoặc không khớp thiết bị.')
    return {key: rule.get(key) for key in ('rule_id', 'version', 'severity', 'auto_close')}


def validate_ml_device(doc, method=None):
    old = doc.get_doc_before_save()
    changed = bool(doc.get(ML_FIELD)) != bool(old.get(ML_FIELD) if old else False)
    if changed:
        require_manager()
    if doc.get(ML_FIELD):
        try:
            profile_task(frappe.get_doc('Device Profile', doc.device_profile))
            site_org = frappe.db.get_value('Site', doc.site, 'organization')
            get_device_read_mapping(doc, site_org)
        except ValueError as exc:
            frappe.throw(str(exc), frappe.ValidationError)


def device_saved(doc, method=None):
    if not doc.get(ML_FIELD):
        return
    task = profile_task(frappe.get_doc('Device Profile', doc.device_profile))
    name = rule_identity(task, doc.name)
    if not frappe.db.exists('Rule Configuration', name):
        # This hook runs inside the same manager-authorized Device transaction.
        frappe.get_doc({
            'doctype': 'Rule Configuration', 'rule_id': name, 'rule_type': 'STATE_CHANGE',
            'scope_type': 'Device', 'scope_ref': doc.name, 'event_type': EVENT,
            'active': 1, 'auto_close': 1, 'severity': 'Medium', 'version': 1,
            'description': 'ML dữ liệu thật, model thử nghiệm; chỉ nhận tín hiệu đã xác nhận. Không thay rule ngưỡng an toàn.',
        }).insert(ignore_permissions=True)
    ml_rule(doc, task)


def list_ml_devices(org_code, after='', page_size=100):
    require_manager_or_data_plane()
    if not isinstance(org_code, str) or not org_code or org_code != org_code.strip():
        frappe.throw('org_code is required', frappe.ValidationError)
    if is_data_plane() and not frappe.db.exists('User Permission', {
        'user': frappe.session.user, 'allow': 'Organization', 'for_value': org_code,
        'apply_to_all_doctypes': 1,
    }):
        frappe.throw('Organization is outside permitted scope', frappe.PermissionError)
    try:
        size = int(page_size)
        if not 1 <= size <= 100 or not isinstance(after, str) or len(after) > 140:
            raise ValueError()
    except (ValueError, TypeError):
        frappe.throw('Invalid pagination', frappe.ValidationError)
    sites = frappe.get_all('Site', filters={'organization': org_code}, pluck='name')
    if not sites:
        return {'schema_version': 1, 'org_code': org_code, 'devices': [], 'rejected': [], 'next_cursor': None}
    rows = frappe.get_all('Device', filters={ML_FIELD: 1, 'status': 'Active',
                          'site': ['in', sites], 'name': ['>', after]},
                          fields=['name'], order_by='name asc', limit_page_length=size+1)
    result, rejected = [], []
    for row in rows[:size]:
        device = frappe.get_doc('Device', row.name)
        # Recheck after selection, so a concurrent move cannot leak a mapping.
        if (not device.get(ML_FIELD) or device.status != 'Active'
                or frappe.db.get_value('Site', device.site, 'organization') != org_code):
            continue
        try:
            task = profile_task(frappe.get_doc('Device Profile', device.device_profile))
            mapping = get_device_read_mapping(device, org_code)
            rule = ml_rule(device, task)
            result.append({'device_id': mapping.local_device_id, 'org_code': mapping.local_org_code,
                           'remote_device_id': mapping.remote_device_id, 'remote_org_code': mapping.remote_org_code,
                           'profile': device.device_profile, 'task_type': task, 'model_key': task, 'rule': rule})
        except ValueError as exc:
            rejected.append({'device_id': device.name, 'message': str(exc)})
    return {'schema_version': 1, 'org_code': org_code, 'devices': result, 'rejected': rejected,
            'next_cursor': rows[size-1].name if len(rows) > size else None}
