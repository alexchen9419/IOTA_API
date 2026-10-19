"""Pure state transition rules, shared by service and unit tests."""
from datetime import timedelta
import hmac

class Conflict(Exception):
    pass

def transition(row, action, body, now, max_retries=5):
    if not hmac.compare_digest(str(row.get('claim_token') or ''), body['claim_token']):
        raise Conflict('stale claim token')
    if row.get('lease_until') is None or row['lease_until'] <= now:
        raise Conflict('lease expired; claim again before reporting')
    if row['status'] not in ('PROCESSING', 'SUBMITTED'):
        raise Conflict('event is not held by an active worker')
    if action in ('submitted', 'confirm'):
        old = row.get('ledger_reference')
        if old and old != body['ledger_reference']:
            raise Conflict('ledger reference differs from recorded transaction')
        result = {'status': 'SUBMITTED' if action == 'submitted' else 'CONFIRMED',
                  'ledger_reference': body['ledger_reference']}
        if action == 'submitted':
            result['submitted_at'] = row.get('submitted_at') or now
        else:
            # Recovery may confirm an already-existing anchor directly.
            result.update(submitted_at=row.get('submitted_at') or now,
                          confirmed_at=now, confirmation_method=body['confirmation_method'],
                          lease_until=None, next_retry_at=None, last_error=None)
        return result
    if action != 'fail':
        raise ValueError('unknown action')
    count = row['retry_count'] + 1
    retry = body['retryable'] and count < max_retries
    return {'status': 'RETRY' if retry else 'DEAD_LETTER', 'retry_count': count,
            'last_error': body['error'], 'lease_until': None,
            'next_retry_at': now + timedelta(seconds=min(3600, 10 * 2 ** min(count - 1, 9))) if retry else None}
