# Upstream-only OPNsense monitor — rollout gates

PR #6666 remains draft. The custom image/patch/workflow and baseline analyzer were
removed in `9f044df6c`; no image publication is needed. The [spec](../specs/2026-11-07-opnsense-spamhaus-mcp.md)
defines the limited configuration/collection contract and exact future runtime paths.
Nothing is deployed or scheduled. User approved pushing the verified review fixes
and replying to/resolving addressed threads; no merge or deployment is authorized.

## Verified prerequisites

- Docker Hub `uhlenheide/opnsense-mcp-server:0.4.2` index:
  `sha256:b6d7f23acc60796756b97b6a45fd665d5c866c1f97a0b8d1e4a0266cef9145fb`.
  Runnable platform is linux/amd64; child manifest:
  `sha256:68450a5c1a750c402c9fc9a01be0139e26f37017ef23cab9bd6eaee4ac635171`.
- Retrieved BuildKit provenance identifies source
  `https://github.com/lucamarien/opnsense-mcp-server` at
  `e8e24749470c5d3e6419e5322f698eeee920396d`. This is not signature verification.
- Unchanged image catalog inspection found the three selected tools among 81.
  Synthetic transport inspection confirmed firmware detection precedes the alias,
  rule and log reads, selects snake_case for 26.7, and stops on firmware denial.
- Existing UID 2004 has exactly eight approved direct/effective privileges,
  retaining deny-config-write and no groups/admin/shell/authorized keys. The
  corrected native ACL query (leading `/api/`) permits firmware status. No
  authenticated stock-tool acceptance is claimed. Do not regenerate credentials.
- Live CRD inspection confirmed `podTemplateSpec`, `toolConfigRef` and Secret refs.
  Peer labels distinguish proxy/backend (`toolhive-name`) from stdio workload
  (`toolhive-basename`); unified uses `toolhive-name=unified`. Proxy port is 8080
  with operator `/health` probes. DNS pods use `k8s-app=kube-dns` in kube-system.
- `${OPNSENSE_IP}` comes from cluster-settings and is injected by the Flux child
  Kustomization patch. Both hostAliases and egress use it; TLS remains verified.

## Review and focused verification

Current findings: preserve same-node CoreDNS replies without a label-only bypass
([4506651140](https://github.com/Tanguille/cluster/pull/6666#discussion_r4506651140));
use the shared firewall address
([4506651143](https://github.com/Tanguille/cluster/pull/6666#discussion_r4506651143)).
The DNS exemption is namespace-qualified and still denies TCP/8080. Other pod
identities and world/remote-node/API-server ingress remain denied. This is policy
intent, not live dataplane evidence.

Run the focused suite (also collected by the existing scripts CI workflow):

```sh
mise exec -- python3 -m unittest discover -s scripts -p test_opnsense_monitor.py -v
```

It includes the 14 unchanged monitor tests and policy/address regressions. Parent
reviewed the production diff and passed full repository validation on these fixes:
Flux, shell, secret guards and 42 regression tests. Reuse that evidence rather
than rerunning the full suite for commit/push. Never decrypt the Secret for tests.

## Remaining approval and acceptance gates

1. Review, full validation and push approval completed; keep PR draft until ready.
   No account change or deployment is part of resolving review remarks.
2. Separate merge/rollout approval. Verify actual amd64/gVisor pod lifecycle,
   hostAliases TLS, authenticated stock reads and result shapes through both
   filters (known proxy `resultType` caveat). Test forbidden tools at dispatch,
   non-unified proxy denial, same-node DNS replies and restricted backend egress.
3. Separate Hermes installation/cron approval. Verify `hermes cron create --help`
   and every intended flag before use: every 15 minutes, `--no-agent`,
   `--catch-up off`, paused creation, `--failure-deliver` and `--paused-reason`.
   Inspect any existing job first; do not duplicate it. Verify run/pause/resume help.
   Alert/failure target: `discord:1509648802678702203:1531266425594056777`.
4. While paused, demonstrate zero-byte healthy output and one alert/recovery pair;
   only resume after separate acceptance. No current runtime script/state/cron install.

Rollback after authorized rollout: pause the job first, then revert monitor GitOps
changes with approval. Never alter Spamhaus firewall policy as monitor rollback.
