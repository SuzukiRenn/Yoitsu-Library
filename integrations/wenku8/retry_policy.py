"""Pure scheduling policy shared by the app worker and root installation tool."""
from datetime import datetime, timedelta, timezone

CHINA = timezone(timedelta(hours=8))
TRANSIENT_CAUSES = frozenset({'TimeoutError', 'ConnectionResetError', 'ConnectionAbortedError',
    'ConnectionRefusedError', 'SSLEOFError', 'RemoteDisconnected', 'gaierror', 'IncompleteRead'})


def transient(record):
    return (record.get('result') == 'transport_failed' and record.get('status') in (None, 200)
            and record.get('cause_class', record.get('error_class')) in TRANSIENT_CAUSES)


def next_check(now=None):
    local = (now or datetime.now(timezone.utc)).astimezone(CHINA)
    return (local+timedelta(days=1)).replace(hour=4, minute=0, second=0, microsecond=0).isoformat()


def deferred(state, reason, now=None):
    now = now or datetime.now(timezone.utc)
    return {**state, 'status': 'network_wait', 'reason': reason,
            'stopped_at': now.isoformat(), 'next_attempt_at': next_check(now)}


def reviewed_initial_network_stop(state):
    attempts = state.get('requests', [])[-3:]
    return (state.get('status') == 'needs_attention'
            and state.get('reason') == 'source_transport_failed_after_bounded_retries'
            and not state.get('items') and not state.get('last_success_at')
            and len(attempts) == 3 and all(transient(item) for item in attempts)
            and len({item.get('url') for item in attempts}) == 1
            and not any(item.get('status') in (401, 403, 429) for item in state.get('requests', [])))
