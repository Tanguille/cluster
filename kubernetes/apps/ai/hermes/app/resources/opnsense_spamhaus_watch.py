#!/usr/bin/env python3
"""Limited stock-tool configuration/collection alerts; not installed or scheduled.

Configuration presence is not enforcement. Recent logs are an incomplete general
firewall sample, never Spamhaus hits or a rate. Silent when unchanged/healthy.
"""
import asyncio
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import fcntl
import json
import os
import signal
from pathlib import Path
import tempfile

TOOLS = ('opn_list_firewall_aliases', 'opn_list_firewall_rules', 'opn_firewall_log')
ALIAS = 'Spamhaus_DROP'
MARKER = 'SDROP-20261106-v1'
ADAPTER = '/opt/data/.hermes/scripts/toolhive_retry.py'
STATE = Path('/opt/data/cron/state/opnsense-spamhaus-watch.json')


def unwrap(value):
    if len(json.dumps(value)) > 2 * 1024 * 1024:
        raise ValueError('Oversized response')
    for _ in range(4):
        if not isinstance(value, dict) or value.get('isError') or 'error' in value:
            raise ValueError('Unavailable response')
        if 'rows' in value or 'entries' in value:
            return value
        if 'structuredContent' in value:
            value = value['structuredContent']
        else:
            parts = value.get('content', [])
            if len(parts) != 1 or parts[0].get('type') != 'text':
                raise ValueError('Unsupported response')
            value = json.loads(parts[0]['text'])
    raise ValueError('Unsupported envelope depth')


def collect(call):
    result = {'coverage': 'configuration observations and recent general firewall log sample only',
              'live_table_count': 'unsupported', 'per_rule_counters': 'unsupported',
              'refresh_success': 'unknown', 'spamhaus_hits': 'unsupported'}
    requests = (
        ('alias_configuration', TOOLS[0], {'search': ALIAS, 'limit': 100}),
        ('rule_configuration', TOOLS[1], {'search': MARKER, 'limit': 100}),
        ('recent_firewall_sample', TOOLS[2], {'limit': 100}),
    )
    for key, tool, args in requests:
        try:
            data = unwrap(call(tool, args))
            rows = data['entries' if key == 'recent_firewall_sample' else 'rows']
            if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(r, dict) for r in rows):
                raise ValueError('Invalid rows')
            total = data['total']
            if type(total) is not int or total < len(rows):
                raise ValueError('Invalid total')
            if key == 'recent_firewall_sample':
                result[key] = {'status': 'observed', 'returned_rows': len(rows),
                               'blocked_rows': sum(r.get('action') == 'block' for r in rows),
                               'coverage': 'partial sample; no attribution, rate or continuity guarantee'}
            else:
                if total != len(rows):
                    raise ValueError('Truncated configuration search')
                if key == 'alias_configuration':
                    matching_rows = [r for r in rows if r.get('name') == ALIAS]
                else:
                    matching_rows = [r for r in rows if MARKER in str(r.get('description', ''))]
                result[key] = {'status': 'observed', 'matching_definitions': len(matching_rows),
                               'coverage': 'search response only; not effective policy or health'}
                if key == 'rule_configuration':
                    result[key]['enabled_definitions'] = sum(
                        str(r.get('enabled')) == '1' for r in matching_rows)
        except Exception:
            result[key] = {'status': 'unavailable'}
    return result


def runtime_adapter():
    spec = importlib.util.spec_from_file_location('hermes_toolhive_retry', ADAPTER)
    module = importlib.util.module_from_spec(spec)
    with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
        spec.loader.exec_module(module)

    def call(name, args):
        if name not in TOOLS:
            raise ValueError('Unapproved tool')
        exact = 'opnsense_' + name
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            success, value, _, _ = asyncio.run(asyncio.wait_for(module.call_toolhive(
                'find_tool', {'tool_description': exact, 'tool_keywords': ['opnsense', name]},
                max_retries=1, backoff=0), timeout=20))
            if success is not True or not isinstance(value, dict) or value.get('isError') or 'error' in value:
                raise ValueError('Discovery unavailable')
            tools = value.get('tools')
            if not isinstance(tools, list) or len(tools) > 100:
                raise ValueError('Invalid discovery')
            if sum(isinstance(t, dict) and t.get('name') == exact for t in tools) != 1:
                raise ValueError('Exact tool unavailable')
            return module._raw_call_tool(exact, args)
    return call


class Deadline(BaseException):
    """Global deadline must not be swallowed by per-signal failures."""


def transitions(snapshot, previous):
    """Only configuration/collection incidents; no log-rate or traffic judgement."""
    active = dict(previous)
    checks = {}
    for key in ('alias_configuration', 'rule_configuration', 'recent_firewall_sample'):
        observed = snapshot.get(key, {}).get('status') == 'observed'
        checks[key + '_collection'] = not observed
        if observed and key == 'alias_configuration':
            checks[key] = snapshot[key]['matching_definitions'] != 1
        if observed and key == 'rule_configuration':
            checks[key] = (snapshot[key]['matching_definitions'] != 3 or snapshot[key]['enabled_definitions'] != 3)
    messages = []
    for key, failed in checks.items():
        if failed != previous.get(key, False):
            verb = 'ALERT' if failed else 'RECOVERY'
            messages.append(f'{verb}: OPNsense {key}; limited configuration/collection evidence only, not traffic health.')
        active[key] = failed
    return active, messages


def run_once(call, path=STATE):
    # Existing directory must be provisioned during separately approved installation.
    # Lock covers collection and replace; no raw logs/configuration stored in state.
    with open(str(path) + '.lock', 'a', encoding='utf-8') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = {'version': 2, 'incidents': {}}
        if path.exists():
            if path.stat().st_size > 8192:
                raise ValueError('Oversized state')
            state = json.loads(path.read_text())
            allowed = {'alias_configuration', 'rule_configuration', 'alias_configuration_collection',
                       'rule_configuration_collection', 'recent_firewall_sample_collection'}
            if state.get('version') != 2 or not isinstance(state.get('incidents'), dict):
                raise ValueError('Incompatible state; explicit migration required')
            if not set(state['incidents']) <= allowed or any(type(v) is not bool for v in state['incidents'].values()):
                raise ValueError('Invalid incidents')
        try:
            snapshot = collect(call)
        except Deadline:
            snapshot = {}  # collection unavailable; preserve prior configuration incidents
        active, messages = transitions(snapshot, state['incidents'])
        fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as output:
                json.dump({'version': 2, 'incidents': active}, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        for message in messages:
            print(message)
    return 0


def main():
    def expired(*_):
        raise Deadline()
    previous = signal.signal(signal.SIGALRM, expired)
    timer = signal.setitimer(signal.ITIMER_REAL, 120)
    try:
        # Adapter failure occurs inside collection, so it becomes a deduped incident.
        adapter = None
        def call(name, args):
            nonlocal adapter
            if adapter is None:
                adapter = runtime_adapter()
            return adapter(name, args)
        return run_once(call)
    except (Exception, Deadline):
        print('Limited OPNsense observation unavailable; no health conclusion.')
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, *timer)
        signal.signal(signal.SIGALRM, previous)


if __name__ == '__main__':
    raise SystemExit(main())
