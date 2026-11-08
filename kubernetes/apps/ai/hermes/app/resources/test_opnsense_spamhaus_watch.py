"""Focused tests of limited stock-tool observations, not firewall health."""
import unittest
import contextlib
import io
import tempfile
from pathlib import Path
import opnsense_spamhaus_watch as watch


class LimitedObservations(unittest.TestCase):
    def sample(self, name, args):
        if name.endswith('aliases'):
            return {'total': 1, 'rows': [{'name': 'Spamhaus_DROP', 'type': 'urltable'}]}
        if name.endswith('rules'):
            return {'total': 1, 'rows': [{'description': 'SDROP-20261106-v1 inbound'}]}
        return {'total': 200, 'entries': [{'action': 'block'}, {'action': 'pass'}]}

    def test_limited_report(self):
        result = watch.collect(self.sample)
        self.assertEqual(result['alias_configuration']['matching_definitions'], 1)
        self.assertEqual(result['rule_configuration']['matching_definitions'], 1)
        self.assertEqual(result['recent_firewall_sample']['returned_rows'], 2)
        self.assertEqual(result['recent_firewall_sample']['blocked_rows'], 1)
        self.assertNotIn('Spamhaus', str(result['recent_firewall_sample']))
        self.assertEqual(result['refresh_success'], 'unknown')

    def test_truncated_configuration_is_not_complete(self):
        def call(name, args):
            data = self.sample(name, args)
            data['total'] = 300
            return data
        self.assertEqual(watch.collect(call)['alias_configuration']['status'], 'unavailable')

    def test_failure_does_not_prevent_independent_reads(self):
        def call(name, args):
            if name.endswith('aliases'):
                raise RuntimeError('private error body')
            return self.sample(name, args)
        result = watch.collect(call)
        self.assertEqual(result['alias_configuration']['status'], 'unavailable')
        self.assertEqual(result['recent_firewall_sample']['status'], 'observed')
        self.assertNotIn('private', str(result))

    def test_no_fuzzy_alias_match(self):
        def call(name, args):
            data = self.sample(name, args)
            if name.endswith('aliases'):
                data['rows'][0]['name'] = 'Not_Spamhaus_DROP'
            return data
        self.assertEqual(watch.collect(call)['alias_configuration']['matching_definitions'], 0)

    def test_raw_logs_not_emitted(self):
        def call(name, args):
            if name.endswith('log'):
                return {'total': 1, 'entries': [{'src': 'private-address', 'action': 'block'}]}
            return self.sample(name, args)
        self.assertNotIn('private-address', str(watch.collect(call)))

    def test_error_envelope_rejected(self):
        with self.assertRaises(ValueError):
            watch.unwrap({'isError': True, 'content': []})

    def test_json_text_envelope(self):
        self.assertEqual(watch.unwrap({'content': [{'type': 'text', 'text': '{"rows": []}'}]}), {'rows': []})

    def test_oversized_log_sample_rejected(self):
        def call(name, args):
            if name.endswith('log'):
                return {'total': 101, 'entries': [{}] * 101}
            return self.sample(name, args)
        self.assertEqual(watch.collect(call)['recent_firewall_sample']['status'], 'unavailable')

    def test_only_fixed_stock_calls(self):
        seen = []
        def call(name, args):
            seen.append((name, args))
            return self.sample(name, args)
        watch.collect(call)
        self.assertEqual(tuple(name for name, _ in seen), watch.TOOLS)
        self.assertTrue(all(args['limit'] == 100 for _, args in seen))

    def healthy(self, name, args):
        if name.endswith('rules'):
            return {'total': 3, 'rows': [{'description': 'SDROP-20261106-v1', 'enabled': '1'} for _ in range(3)]}
        return self.sample(name, args)

    def test_healthy_run_is_silent(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            path = Path(directory) / 'state.json'
            watch.run_once(self.healthy, path)
            watch.run_once(self.healthy, path)
            self.assertEqual(output.getvalue(), '')

    def test_alert_dedupe_recovery(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            path = Path(directory) / 'state.json'
            watch.run_once(self.sample, path)
            watch.run_once(self.sample, path)
            watch.run_once(self.healthy, path)
            watch.run_once(self.healthy, path)
            self.assertEqual(output.getvalue().count('ALERT:'), 1)
            self.assertEqual(output.getvalue().count('RECOVERY:'), 1)

    def test_unknown_does_not_recover_configuration_incident(self):
        old = {'rule_configuration': True}
        active, messages = watch.transitions({}, old)
        self.assertTrue(active['rule_configuration'])
        self.assertFalse(any('RECOVERY' in m for m in messages))

    def test_unknown_logs_do_not_prevent_configuration_recovery(self):
        snapshot = watch.collect(self.healthy)
        snapshot['recent_firewall_sample'] = {'status': 'unavailable'}
        active, messages = watch.transitions(snapshot, {'rule_configuration': True})
        self.assertFalse(active['rule_configuration'])
        self.assertTrue(any('RECOVERY: OPNsense rule_configuration;' in m for m in messages))
        self.assertTrue(active['recent_firewall_sample_collection'])

    def test_collection_failure_deduped(self):
        def unavailable(*_):
            raise ValueError('private response')
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            path = Path(directory) / 'state.json'
            watch.run_once(unavailable, path)
            watch.run_once(unavailable, path)
            self.assertEqual(output.getvalue().count('ALERT:'), 3)
            self.assertNotIn('private', output.getvalue())


if __name__ == '__main__':
    unittest.main()
