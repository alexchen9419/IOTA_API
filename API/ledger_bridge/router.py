import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import timedelta
from typing import Annotated
import pymysql
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, StrictBool
from .db import transaction
from .state import Conflict, transition

log = logging.getLogger(__name__)

def authenticate(authorization: Annotated[str | None, Header()] = None):
    expected = os.environ.get('LEDGER_WORKER_TOKEN', '')
    if len(expected) < 32:
        raise HTTPException(503, 'Ledger service token not configured (minimum 32 characters)')
    scheme, _, value = (authorization or '').partition(' ')
    if scheme.lower() != 'bearer' or not hmac.compare_digest(value.encode(), expected.encode()):
        raise HTTPException(401, 'Invalid service token', headers={'WWW-Authenticate': 'Bearer'})

router = APIRouter(prefix='/ledger', tags=['Ledger Bridge'], dependencies=[Depends(authenticate)])

class Claim(BaseModel):
    preview: StrictBool = False
    worker_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=1, ge=1, le=20, strict=True)
    lease_seconds: int = Field(default=300, ge=15, le=3600, strict=True)

class Report(BaseModel):
    claim_token: str = Field(pattern=r'^[0-9a-f]{64}$')

class Submitted(Report):
    ledger_reference: str = Field(min_length=1, max_length=255)

class Confirm(Submitted):
    confirmation_method: str = Field(min_length=1, max_length=128)

class Fail(Report):
    error: str = Field(min_length=1, max_length=500)
    retryable: StrictBool

def db_error(exc):
    log.error('Ledger database operation failed (%s)', type(exc).__name__)
    raise HTTPException(503, 'Ledger database unavailable or migration required') from exc

@router.get('/health')
def health():
    try:
        with transaction() as cur:
            # Check migrated fields/table as well as connectivity, without changing state.
            cur.execute('SELECT claim_token, lease_until, next_retry_at, confirmation_method FROM ledger_events LIMIT 0')
            cur.execute('SELECT event_id FROM ledger_bridge_receipts LIMIT 0')
            cur.execute('SELECT status, COUNT(*) AS n FROM ledger_events GROUP BY status')
            counts = {r['status']: r['n'] for r in cur.fetchall()}
        return {'ok': True, 'protocol_version': '1.1', 'claim_token_required': True, 'counts': counts}
    except pymysql.MySQLError as exc:
        db_error(exc)

@router.post('/claim')
def claim(body: Claim):
    try:
        with transaction() as cur:
            # InnoDB row locks serialize claims; do not select then update outside a transaction.
            cur.execute('''SELECT * FROM ledger_events WHERE
                status='PENDING'
                OR (status='RETRY' AND (next_retry_at IS NULL OR next_retry_at<=UTC_TIMESTAMP(6)))
                OR (status IN ('PROCESSING','SUBMITTED') AND (lease_until IS NULL OR lease_until<=UTC_TIMESTAMP(6)))
                ORDER BY created_at, event_id LIMIT %s FOR UPDATE''', (body.limit,))
            rows = cur.fetchall()
            cur.execute('SELECT UTC_TIMESTAMP(6) AS now')
            now = cur.fetchone()['now']
            until = now + timedelta(seconds=body.lease_seconds)
            events = []
            for row in rows:
                previous = row['status']
                token = secrets.token_hex(32)
                if not body.preview:
                    cur.execute('''UPDATE ledger_events SET status='PROCESSING', worker_id=%s,
                    claim_token=%s, lease_until=%s, next_retry_at=NULL WHERE event_id=%s''',
                    (body.worker_id, token, until, row['event_id']))
                # Preserve reference/timestamps on recovery; never change canonical payload/hash.
                events.append({k: row.get(k) for k in ('event_id','payload','payload_hash',
                    'uc_id','event_type','family_id','gateway_id','device_id','ledger_reference','retry_count')})
                events[-1].update(status=previous if body.preview else 'PROCESSING', previous_status=previous,
                    recovery_required=previous!='PENDING', claim_token=None if body.preview else token,
                    lease_until=None if body.preview else until.isoformat()+'Z')
        return {'events': events, 'preview': body.preview}
    except pymysql.MySQLError as exc:
        db_error(exc)


def record(eid, action, model):
    body = model.model_dump()
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    try:
        with transaction() as cur:
            cur.execute('SELECT * FROM ledger_events WHERE event_id=%s FOR UPDATE', (eid,))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(404, 'Unknown event')
            cur.execute('''SELECT request_hash, response FROM ledger_bridge_receipts
                WHERE event_id=%s AND claim_token=%s AND action=%s''', (eid, body['claim_token'], action))
            receipt = cur.fetchone()
            if receipt:
                if receipt['request_hash'] != digest:
                    raise HTTPException(409, 'Report already recorded with different content')
                response = receipt['response']
                return json.loads(response) if isinstance(response, str) else response
            cur.execute('SELECT UTC_TIMESTAMP(6) AS now')
            now = cur.fetchone()['now']
            try:
                changes = transition(row, action, body, now)
            except Conflict as exc:
                raise HTTPException(409, str(exc)) from exc
            cur.execute('UPDATE ledger_events SET ' + ', '.join(f'`{k}`=%s' for k in changes)
                        + ' WHERE event_id=%s', tuple(changes.values()) + (eid,))
            result = {'ok': True, 'event_id': eid, 'status': changes['status']}
            cur.execute('''INSERT INTO ledger_bridge_receipts
                (event_id, claim_token, action, request_hash, response) VALUES (%s,%s,%s,%s,%s)''',
                (eid, body['claim_token'], action, digest, json.dumps(result)))
        return result
    except pymysql.MySQLError as exc:
        db_error(exc)

@router.post('/{event_id}/submitted')
def submitted(event_id: str, body: Submitted):
    return record(event_id, 'submitted', body)

@router.post('/{event_id}/confirm')
def confirm(event_id: str, body: Confirm):
    return record(event_id, 'confirm', body)

@router.post('/{event_id}/fail')
def fail(event_id: str, body: Fail):
    return record(event_id, 'fail', body)
