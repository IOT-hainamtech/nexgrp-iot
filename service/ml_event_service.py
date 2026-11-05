"""Bounded, organization-scoped wakeup hints; Data Plane stays authoritative."""
import hashlib
import json
import re
from datetime import datetime, timezone

import frappe

from nextgrp.nextgrp_iot.service.integration_security import (
    is_data_plane, require_manager_or_data_plane,
)
from nextgrp.nextgrp_iot.service.ml_registry_service import profile_task

CURSOR = re.compile(r'^\d{1,20}-\d{1,20}$')
MAX_EVENTS = 5000


def stream_key(org):
    return frappe.cache().make_key('nextgrp:ml:door:'+hashlib.sha256(org.encode()).hexdigest())


def _text(value):
    return value.decode('utf-8') if isinstance(value, bytes) else str(value)


def _allowed(device_id, org):
    try:
        device = frappe.get_doc('Device', device_id)
    except frappe.DoesNotExistError:
        return False
    if (not device.get('custom_ml_enabled') or device.status != 'Active'
            or frappe.db.get_value('Site', device.site, 'organization') != org):
        return False
    try:
        return profile_task(frappe.get_doc('Device Profile', device.device_profile)) == 's6_anomaly'
    except (ValueError,frappe.DoesNotExistError):
        return False


def schedule_ml_wakeup(device, org, payload):
    """Never let an optional ML channel break the existing event receiver."""
    if payload.get('event_type') not in ('DOOR_OPENED','DOOR_CLOSED'):
        return
    if not device.get('custom_ml_enabled') or device.status != 'Active':
        return
    try:
        if profile_task(frappe.get_doc('Device Profile',device.device_profile)) != 's6_anomaly':
            return
        hint = {key:payload[key] for key in ('device_id','event_id','event_type','event_at')}
        hint['queued_at'] = datetime.now(timezone.utc).isoformat()
        # Only after the source POST commits: no wakeups for rolled-back events.
        frappe.db.after_commit.add(lambda: _publish(org,hint))
    except Exception as exc:
        frappe.logger('nextgrp_ml').warning('ML wakeup scheduling failed: %s',type(exc).__name__)


def _publish(org, hint):
    try:
        cache = frappe.cache()
        key = stream_key(org)
        cache.xadd(key,{'hint':json.dumps(hint,separators=(',',':'))},maxlen=MAX_EVENTS,approximate=True)
        cache.expire(key,86400)
    except Exception as exc:
        frappe.logger('nextgrp_ml').warning('ML wakeup publication failed: %s',type(exc).__name__)


def wait_ml_events(org_code, cursor='$', timeout=10):
    require_manager_or_data_plane()
    if not isinstance(org_code,str) or not org_code or org_code != org_code.strip() or len(org_code)>140:
        frappe.throw('org_code is required',frappe.ValidationError)
    if is_data_plane() and not frappe.db.exists('User Permission',{
        'user':frappe.session.user,'allow':'Organization','for_value':org_code,'apply_to_all_doctypes':1,
    }):
        frappe.throw('Organization is outside permitted scope',frappe.PermissionError)
    try:
        seconds=int(timeout)
        if not 0 <= seconds <= 10 or not isinstance(cursor,str) or (cursor!='$' and not CURSOR.fullmatch(cursor)):
            raise ValueError()
    except (ValueError,TypeError):
        frappe.throw('Invalid ML event cursor or timeout',frappe.ValidationError)
    cache=frappe.cache()
    key=stream_key(org_code)
    # Resolve '$' once, then return a concrete cursor even on timeout. This
    # prevents losing arrivals between successive long-poll requests.
    if cursor=='$':
        tail=cache.xrevrange(key,count=1)
        cursor=_text(tail[0][0]) if tail else '0-0'
    first=cache.xrange(key,count=1)
    gap=bool(first and cursor!='0-0' and tuple(map(int,cursor.split('-'))) < tuple(map(int,_text(first[0][0]).split('-'))))
    if seconds:
        batches=cache.xread({key:cursor},count=200,block=seconds*1000)
    else:
        batches=cache.xread({key:cursor},count=200)
    hints,permissions=[],{}
    for _, rows in batches:
        for event_cursor, fields in rows:
            cursor=_text(event_cursor)
            raw=fields.get(b'hint',fields.get('hint'))
            try:
                hint=json.loads(raw)
                device=hint['device_id']
                if device not in permissions:
                    permissions[device]=_allowed(device,org_code)
                if permissions[device]:
                    hints.append({**hint,'cursor':cursor})
            except (ValueError,TypeError,KeyError):
                gap=True
    return {'schema_version':1,'org_code':org_code,'cursor':cursor,'gap':gap,'events':hints}
