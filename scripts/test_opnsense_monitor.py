"""PR-scoped policy regressions and the dormant Hermes monitor's tests.

Uses the repository-pinned yq, as the existing scripts regression suite does.
Selector checks model manifest intent, not a substitute for live Cilium tests.
"""
import json
import ipaddress
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
MCP = ROOT / 'kubernetes/apps/ai/toolhive/mcp'


def document(path, index=0):
    return json.loads(subprocess.check_output(
        ['yq', '-o=json', f'select(documentIndex == {index})', str(path)]))


def matches(selector, labels):
    if any(labels.get(key) != value for key, value in selector.get('matchLabels', {}).items()):
        return False
    for item in selector.get('matchExpressions', []):
        key, operator = item['key'], item['operator']
        if operator == 'Exists' and key not in labels:
            return False
        if operator == 'In' and labels.get(key) not in item['values']:
            return False
        if operator == 'NotIn' and labels.get(key) in item['values']:
            return False
        if operator not in {'Exists', 'In', 'NotIn'}:
            raise AssertionError('Unsupported test selector operator')
    return True


class OpnsensePolicyTests(unittest.TestCase):
    def test_dns_reply_exception_cannot_bypass_proxy_isolation(self):
        policy = document(MCP / 'opnsense-networkpolicy.yaml')['spec']
        def denied(labels, port):
            for rule in policy['ingressDeny']:
                if not any(matches(s, labels) for s in rule.get('fromEndpoints', [])):
                    continue
                ports = [p['port'] for group in rule.get('toPorts', []) for p in group['ports']]
                if not ports or str(port) in ports:
                    return True
            return False
        namespace = 'io.kubernetes.pod.namespace'
        cases = [
            ({namespace: 'kube-system', 'k8s-app': 'kube-dns'}, 40000, False),
            ({namespace: 'kube-system', 'k8s-app': 'kube-dns'}, 8080, True),
            ({namespace: 'ai', 'toolhive-name': 'unified'}, 8080, False),
            ({namespace: 'ai', 'k8s-app': 'kube-dns'}, 8080, True),
            ({namespace: 'default', 'k8s-app': 'kube-dns', 'toolhive-name': 'unified'}, 8080, True),
            ({namespace: 'kube-system', 'toolhive-name': 'unified'}, 8080, True),
            ({namespace: 'ai', 'app': 'hermes'}, 8080, True),
            ({}, 8080, False),  # node-local kubelet, not a pod identity
        ]
        for labels, port, expected in cases:
            with self.subTest(labels=labels, port=port):
                self.assertEqual(denied(labels, port), expected)
        entities = {e for r in policy['ingressDeny'] for e in r.get('fromEntities', [])}
        self.assertEqual(entities, {'world', 'remote-node', 'kube-apiserver'})

    def test_firewall_address_has_one_source(self):
        server = document(MCP / 'opnsense.yaml')['spec']
        backend = document(MCP / 'opnsense-networkpolicy.yaml', 1)['spec']
        self.assertEqual(server['podTemplateSpec']['spec']['hostAliases'][0]['ip'], '${OPNSENSE_IP}')
        self.assertEqual(backend['egress'][0]['toCIDR'], ['${OPNSENSE_IP}/32'])
        settings = document(ROOT / 'kubernetes/components/common/cluster-settings.yaml')
        ipaddress.IPv4Address(settings['data']['OPNSENSE_IP'])


def load_tests(loader, tests, pattern):
    # Keep monitor regressions in the existing PR CI job; no new build workflow.
    resources = str(ROOT / 'kubernetes/apps/ai/hermes/app/resources')
    tests.addTests(unittest.TestLoader().discover(
        resources, pattern='test_opnsense_spamhaus_watch.py', top_level_dir=resources))
    return tests


if __name__ == '__main__':
    unittest.main()
