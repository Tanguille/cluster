#!/usr/bin/env python3
"""Deterministic Spamhaus evidence analyzer. No firewall credentials or direct HTTP.

Uses the installed Hermes ToolHive adapter, with exact-name discovery and no
direct firewall connection. No scheduler or runtime installation is performed.
"""
import copy
import asyncio
import datetime as dt
import fcntl
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import signal
import statistics
import tempfile
import time
from contextlib import contextmanager, redirect_stdout, redirect_stderr

STATE = Path("/opt/data/cron/state/opnsense-spamhaus-watch.json")
BASELINE = STATE.with_name("opnsense-spamhaus-baseline.json")
MARKER = "SDROP-20261106-v1"
TOOLS = tuple("opn_monitor_" + name for name in (
    "alias_config", "alias_contents", "table_metadata", "rules", "rule_stats", "logs"))
GATEWAY_NAMES = {name: "opnsense_" + name for name in TOOLS}
ADAPTER = Path("/opt/data/.hermes/scripts/toolhive_retry.py")


def timestamp(value):
    stamp = dt.datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("Timestamp timezone unknown")
    return stamp


def number(value, maximum=2**63-1):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("Invalid numeric evidence")
    return value


def new_state():
    return {"schema_version":1,"coverage_notice":False,"refresh_success":"unknown",
            "last_success":None,"last_attempt":None,"counters":{},"log_history":[],
            "incidents":{},"outbound_zero_polls":0,"last_digest":None,"baseline":None,
            "observed_families":[],"last_log_at":None,"pending_log_anchor":None}


class SnapshotTimeout(TimeoutError):
    """Stop the entire snapshot, not only the currently running signal."""


@contextmanager
def deadline(seconds):
    def expired(*_):
        raise SnapshotTimeout("Snapshot deadline exceeded")
    old = signal.signal(signal.SIGALRM, expired)
    previous = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous)
        signal.signal(signal.SIGALRM, old)


def collect_snapshot(call_tool, now):
    """call_tool(exact_name, args) -> schema object; discover first in adapter.

    Signal deadline bounds blocking adapter calls too; run in main thread on Linux.
    No tool response is ever used as a command, prompt, endpoint, or tool name.
    """
    result = {"schema_version":1,"at":now.isoformat(),"complete":False,
              "errors":[],"log_complete":False,"log_new_count":None,
              "log_saturated":False,"refresh_success":"unknown",
              "signals":{key:False for key in ("alias","table","rules","counters","logs")}}
    expires = time.monotonic()+120

    def call(suffix, **args):
        if time.monotonic()>=expires:
            raise SnapshotTimeout()
        value = call_tool("opn_monitor_" + suffix, args)
        if time.monotonic()>=expires:
            raise SnapshotTimeout()
        if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("isError") or "error" in value:
            raise ValueError("Tool evidence unavailable")
        return value

    def pages(suffix, size, bound, identity):
        rows, seen, total = [], set(), None
        for page in range(1,bound+1):
            data = call(suffix,page=page,page_size=size)
            count = number(data["total"],size*bound)
            if total is None:
                total = count
            if count != total or data["current"] != page:
                raise ValueError("Moving pagination")
            batch = data["rows"]
            expected = min(size,total-len(rows))
            if not isinstance(batch,list) or len(batch) != expected or number(data["rowCount"],size) != len(batch):
                raise ValueError("Incomplete pagination")
            for row in batch:
                if identity=="uuid" and row.get("legacy") is True:
                    # non_mvc_rules maps label -> uuid (not unique), but emits a
                    # unique priority/sequence sort_order for every legacy row.
                    order = row.get("sort_order")
                    if not isinstance(order,str) or not order or MARKER in row.get("description",""):
                        raise ValueError("Ambiguous legacy rule identity")
                    key = ("legacy",order)
                else:
                    value = row[identity]
                    if not isinstance(value,str) or not value:
                        raise ValueError("Missing strict row identity")
                    key = (identity,value)
                if key in seen:
                    raise ValueError("Duplicate pagination")
                seen.add(key)
            rows.extend(batch)
            if len(rows) == total:
                end = call(suffix,page=1,page_size=size)
                # Count and first-page membership checked again, not an atomic snapshot claim.
                if end["total"] != total or end["current"] != 1 or end["rowCount"] != min(size,total) or end["rows"] != rows[:size]:
                    raise ValueError("Moving pagination")
                return rows
        raise ValueError("Pagination bound exhausted")

    def alias_signal():
        result["alias"] = call("alias_config")["alias"]
        if result["alias"] is not None and not isinstance(result["alias"],dict):
            raise ValueError("Unsupported alias configuration")

    def table_signal():
        entries = pages("alias_contents",500,100,"ip")
        result["count"] = len(entries)
        result["families"] = sorted({ipaddress.ip_network(r["ip"],strict=False).version for r in entries})
        if call("table_metadata").get("refresh_success") != "unknown":
            raise ValueError("Unsupported freshness evidence")

    def rules_signal():
        result["rules"] = [r for r in pages("rules",100,50,"uuid") if MARKER in r.get("description","")]

    def counter_signal():
        stats = call("rule_stats")
        if not isinstance(stats["rule_ids"],list) or len(stats["rule_ids"])>5000:
            raise ValueError("Unsupported statistics")
        ids = [r["id"] for r in stats["rule_ids"]]
        if not all(isinstance(key,str) and key for key in ids) or len(set(ids))!=len(ids):
            raise ValueError("Unsupported running identities")
        # Log identity depends on this validated ID list, not on configured-rule
        # collection or counter parsing succeeding.
        result["running_ids"] = ids
        if not isinstance(stats["stats"],dict) or len(stats["stats"])>5000:
            raise ValueError("Unsupported statistics")
        result["stats"] = {}
        for key,sample in stats["stats"].items():
            if not isinstance(key,str) or not key:
                raise ValueError("Unsupported counter identity")
            result["stats"][key] = {k:number(sample[k]) for k in ("packets","pf_rules")}
            if sample["pf_rules"] == 0:
                raise ValueError("Counter has no compiled PF rules")

    def log_signal():
        logs = call("logs",limit=2000)
        rows = logs["rows"]
        if not isinstance(rows,list) or len(rows)>2000 or logs["raw_count"] != len(rows) or type(logs["saturated"]) is not bool:
            raise ValueError("Unsupported logs")
        result["log_saturated"] = logs["saturated"]
        result["log_samples"] = []
        known = set(result.get("running_ids",[]))
        seen = set()
        previous = None
        log_valid = bool(rows)
        for row in rows:
            try:
                stamp = timestamp(row["__timestamp__"])
                digest = row["__digest__"]
                if not isinstance(digest,str) or len(digest)!=32 or any(c not in "0123456789abcdef" for c in digest) or digest in seen or row["rid"] not in known or stamp>now or (previous and stamp>previous):
                    raise ValueError("Unknown log identity/order")
                seen.add(digest)
                previous = stamp
                result["log_samples"].append({"at":stamp.isoformat(),"digest":digest})
            except (ValueError,KeyError,TypeError):
                log_valid=False
                break
        result["log_complete"] = log_valid and not logs["saturated"]
        if not log_valid:
            result["log_samples"] = []

    try:
        with deadline(120):
            timestamp(result["at"])
            for signal_name,collect in (("alias",alias_signal),("table",table_signal),
                                        ("rules",rules_signal),("counters",counter_signal),("logs",log_signal)):
                try:
                    if time.monotonic()>=expires:
                        raise SnapshotTimeout()
                    collect()
                    result["signals"][signal_name] = True
                except SnapshotTimeout:
                    raise
                except Exception:
                    result["errors"].append(signal_name + " evidence unavailable or inconsistent")
            result["complete"] = all(result["signals"].values())
    except Exception:
        result["errors"].append("Snapshot unavailable or deadline exceeded")
    return result


def validate_baseline(baseline):
    if baseline.get("schema_version") != 1 or baseline.get("approved") is not True:
        raise ValueError("Approved baseline required")
    number(baseline["count"],50000)
    if not baseline["rules"] or len(baseline["rules"])>5000 or not set(baseline["families"]).issubset({4,6}):
        raise ValueError("Invalid baseline")
    ids = [r["uuid"] for r in baseline["rules"]]
    if len(set(ids))!=len(ids) or not all(isinstance(r,dict) and MARKER in r.get("description","") for r in baseline["rules"]):
        raise ValueError("Invalid baseline rules")


def evaluate(snapshot, state, baseline):
    """Pure transition: each incident needs complete evidence for its own signal."""
    validate_baseline(baseline)
    validate_state(state)
    state = copy.deepcopy(state)
    if state["baseline"] is not None and state["baseline"] != baseline:
        raise ValueError("Approved baseline changed; explicit state migration required")
    state["baseline"] = copy.deepcopy(baseline)
    messages = []
    conditions = {}
    log_rebased = False
    if not state["coverage_notice"]:
        messages.append("Spamhaus coverage: successful feed refresh is unknown; cache mtime is not retrieval success. Logs are bounded samples, not an archive.")
        state["coverage_notice"] = True
    now = timestamp(snapshot["at"])
    complete = snapshot.get("complete") is True
    signals = snapshot.get("signals",{key:complete for key in ("alias","table","rules","counters","logs")})
    if set(signals)!={"alias","table","rules","counters","logs"} or not all(type(value) is bool for value in signals.values()):
        raise ValueError("Unsupported signal completeness")
    signals = dict(signals)
    prior_times = [timestamp(state[key]) for key in ("last_attempt","last_success") if state[key]]
    reference = max(prior_times) if prior_times else None
    if reference and now < reference:
        complete = False
        signals = {key:False for key in signals}
    state["last_attempt"] = max(now,reference).isoformat() if reference else snapshot["at"]
    recoverable = set()
    if not complete:
        conditions["collection"] = "Spamhaus collection unavailable/inconsistent; evidence is incomplete."
    else:
        recoverable.add("collection")
    if signals["alias"]:
        recoverable.add("alias")
        alias = snapshot["alias"]
        if alias is None or alias.get("enabled") not in ("1",1,True):
            conditions["alias"] = "Spamhaus alias missing or disabled."
    if signals["table"]:
        recoverable.update(("empty","count","family"))
        count = number(snapshot["count"],50000)
        if count == 0:
            conditions["empty"] = "Spamhaus live table is empty."
        if count < baseline["count"] * .5:
            conditions["count"] = "Spamhaus count fell more than 50% from approved baseline."
        previous_families = set(baseline["families"]) | set(state["observed_families"])
        if previous_families - set(snapshot["families"]):
            conditions["family"] = "Spamhaus lost a previously present IP family."
        state["observed_families"] = sorted(previous_families | set(snapshot["families"]))
    approved = {r["uuid"]:r for r in baseline["rules"]}
    if signals["rules"]:
        recoverable.add("rules")
        current = {r["uuid"]:r for r in snapshot["rules"]}
        if approved != current:
            conditions["rules"] = "Spamhaus configured marked rules changed or are missing; review all approved fields."
    if signals["counters"]:
        recoverable.add("runtime")
        if set(approved) - set(snapshot["running_ids"]) or set(approved) - set(snapshot["stats"]):
            conditions["runtime"] = "Spamhaus marked runtime rule identity/counters are missing."
        deltas = []
        for key,rule in approved.items():
            if rule.get("direction") != "out":
                continue
            sample = snapshot["stats"].get(key)
            previous = state["counters"].get(key)
            if key not in snapshot["running_ids"] or not sample or not previous or sample["pf_rules"] != previous["pf_rules"] or sample["packets"] < previous["packets"]:
                deltas.append(None)
            else:
                deltas.append(sample["packets"] - previous["packets"])
        if any(delta is not None and delta>0 for delta in deltas):
            conditions["outbound"] = "Spamhaus outbound marked-rule packets increased: unusual / worth checking (not a maliciousness verdict)."
            state["outbound_zero_polls"] = 0
        elif deltas and all(delta == 0 for delta in deltas):
            state["outbound_zero_polls"] += 1
        else:
            state["outbound_zero_polls"] = 0
        state["counters"] = {key:sample for key,sample in snapshot["stats"].items() if key in approved}
        if state["outbound_zero_polls"] >= 4:
            recoverable.add("outbound")
    else:
        state["outbound_zero_polls"] = 0
    if signals["logs"]:
        log_complete = snapshot["log_complete"] and not snapshot["log_saturated"]
        new_count = snapshot.get("log_new_count")
        if "log_samples" in snapshot:
            samples = snapshot["log_samples"]
            digests = [s["digest"] for s in samples]
            valid_window = bool(log_complete and samples)
            anchor = state["last_digest"]
            anchor_at = state["last_log_at"] or state["last_success"]
            continuous = bool(valid_window and "coverage" not in state["incidents"] and anchor_at and anchor in digests
                              and timestamp(samples[-1]["at"])<=timestamp(anchor_at))
            if not continuous and valid_window and state["pending_log_anchor"]:
                pending = state["pending_log_anchor"]
                if pending["digest"] in digests and timestamp(samples[-1]["at"])<=timestamp(pending["at"]):
                    anchor = pending["digest"]
                    continuous = True
                    log_rebased = True
            log_complete = continuous
            new_count = digests.index(anchor) if continuous else None
            if continuous:
                state["last_digest"] = digests[0]
                state["last_log_at"] = snapshot["at"]
                state["pending_log_anchor"] = None
            elif valid_window:
                # Explicit two-poll re-baseline: preserve the confirmed head and
                # coverage incident. Never count the missing interval. A later
                # complete overlapping window may prove only this new interval.
                state["pending_log_anchor"] = {"digest":digests[0],"at":snapshot["at"]}
            else:
                state["pending_log_anchor"] = None
        if not log_complete or new_count is None:
            conditions["coverage"] = "Spamhaus log coverage/volume unknown: saturated, missing time coverage, or unresolved identity."
        else:
            recoverable.update(("coverage","volume"))
            number(new_count,2000)
            history = state["log_history"]
            if len(history)>=8 and new_count>=1000 and new_count>5*statistics.median(history):
                conditions["volume"] = "Spamhaus firewall sample volume unusually high; worth checking."
            state["log_history"] = (history+[new_count])[-96:]
    else:
        conditions["coverage"] = "Spamhaus logs unavailable; coverage is unknown."
        state["pending_log_anchor"] = None
    if complete:
        state["last_success"] = snapshot["at"]
    for key,message in conditions.items():
        if key not in state["incidents"]:
            messages.append(message)
        state["incidents"][key] = True
    for key in list(state["incidents"]):
        if key in recoverable and key not in conditions:
            detail = " (new baseline; previous gap remains unknown)" if key=="coverage" and log_rebased else ""
            messages.append("Recovered: Spamhaus " + key + detail + ".")
            del state["incidents"][key]
    state["refresh_success"] = "unknown"
    return state,messages


def validate_state(state):
    if not isinstance(state,dict) or set(state)!=set(new_state()) or state.get("schema_version")!=1:
        raise ValueError("Invalid monitor state")
    if not isinstance(state["incidents"],dict) or not isinstance(state["counters"],dict) or not isinstance(state["log_history"],list) or len(state["log_history"])>96:
        raise ValueError("Invalid monitor state")
    allowed_incidents = {"collection","alias","empty","count","family","rules","runtime","outbound","coverage","volume"}
    if set(state["incidents"])-allowed_incidents or not all(value is True for value in state["incidents"].values()):
        raise ValueError("Invalid incident state")
    if type(state["coverage_notice"]) is not bool or state["refresh_success"] != "unknown":
        raise ValueError("Invalid coverage state")
    digest = state["last_digest"]
    if digest is not None and (not isinstance(digest,str) or len(digest)!=32 or any(c not in "0123456789abcdef" for c in digest)):
        raise ValueError("Invalid digest state")
    pending = state["pending_log_anchor"]
    if pending is not None:
        if not isinstance(pending,dict) or set(pending)!={"digest","at"}:
            raise ValueError("Invalid pending log anchor")
        digest = pending["digest"]
        if not isinstance(digest,str) or len(digest)!=32 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid pending log digest")
        timestamp(pending["at"])
    if not isinstance(state["observed_families"],list) or state["observed_families"] not in ([],[4],[6],[4,6]):
        raise ValueError("Invalid family state")
    for value in state["log_history"]:
        number(value,2000)
    number(state["outbound_zero_polls"])
    if len(state["counters"])>5000 or len(state["incidents"])>16:
        raise ValueError("Invalid monitor state bounds")
    for sample in state["counters"].values():
        if not isinstance(sample,dict) or set(sample)!={"packets","pf_rules"}:
            raise ValueError("Invalid counter state")
        for value in sample.values():
            number(value)
    if state["baseline"] is not None:
        validate_baseline(state["baseline"])
    for key in ("last_success","last_attempt","last_log_at"):
        if state[key] is not None:
            timestamp(state[key])


def load_state(path):
    if not path.exists():
        return new_state()
    if path.stat().st_size>1024*1024:
        raise ValueError("State size exceeded")
    state = json.loads(path.read_text())
    validate_state(state)
    return state


def save_state(path, state):
    validate_state(state)
    encoded = json.dumps(state,sort_keys=True)
    if len(encoded.encode("utf-8")) > 1024*1024:
        raise ValueError("State size exceeded")
    fd, temporary = tempfile.mkstemp(prefix=path.name+".",dir=path.parent)
    try:
        with os.fdopen(fd,"w") as file:
            file.write(encoded)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary,path)
        directory = os.open(path.parent,os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def lock_state(path):
    with open(str(path)+".lock","a",encoding="utf-8") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def emit(messages):
    for message in messages:
        print(message)


def unwrap_tool_result(value):
    """Bounded MCP envelope decoding; error content never becomes evidence."""
    try:
        if len(json.dumps(value)) > 8*1024*1024:
            raise ValueError("Tool response bound exceeded")
        for _ in range(4):
            if not isinstance(value,dict) or value.get("isError") or "error" in value:
                raise ValueError("Tool returned error")
            if value.get("schema_version") == 1:
                return value
            if "structuredContent" in value:
                value = value["structuredContent"]
            else:
                parts = value["content"]
                if not isinstance(parts,list) or len(parts)!=1 or parts[0].get("type")!="text":
                    raise ValueError("Unsupported tool envelope")
                value = json.loads(parts[0]["text"])
    except (KeyError,TypeError,ValueError,AttributeError):
        raise ValueError("Tool response unavailable or unsupported") from None
    raise ValueError("Tool envelope depth exceeded")


def runtime_adapter(module=None):
    """Lazy discovery, within collect_snapshot's deadline; no fuzzy tool names.

    Installed contract inspected read-only 2026-11-07. call_toolhive returns
    (success, parsed JSON, attempts, error); max_retries=1 disables retry sleeps.
    Backend calls use its raw helper because the SDK helper discards outer
    isError, whereas _raw_call_tool checks it before returning parsed content.
    """
    discovered = {}

    def call(name,args):
        nonlocal module
        if name not in GATEWAY_NAMES or not isinstance(args,dict):
            raise ValueError("Unapproved monitor tool")
        # The installed helper prints retry/errors; never send those bodies to Discord.
        with open(os.devnull,"w",encoding="utf-8") as sink, redirect_stdout(sink), redirect_stderr(sink):
            try:
                if module is None:
                    spec = importlib.util.spec_from_file_location("hermes_toolhive_retry",ADAPTER)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                if name not in discovered:
                    response = asyncio.run(module.call_toolhive(
                        "find_tool",{"tool_description":GATEWAY_NAMES[name],
                                     "tool_keywords":["opnsense",name]},max_retries=1,backoff=0))
                    success,value,_,_ = response
                    if success is not True or not isinstance(value,dict) or value.get("isError") or "error" in value:
                        raise ValueError("Discovery unavailable")
                    tools = value["tools"]
                    if not isinstance(tools,list) or len(tools)>100:
                        raise ValueError("Discovery shape unavailable")
                    matches = [tool for tool in tools if isinstance(tool,dict) and tool.get("name")==GATEWAY_NAMES[name]]
                    if len(matches)!=1:
                        raise ValueError("Required exact tool not discovered")
                    discovered[name] = matches[0]["name"]
                value = module._raw_call_tool(discovered[name],args)
                return unwrap_tool_result(value)
            except SnapshotTimeout:
                raise
            except Exception:
                raise ValueError("ToolHive monitor discovery/call unavailable") from None
    return call


def main(call_tool=None, state_path=STATE, baseline_path=BASELINE):
    try:
        with lock_state(state_path):
            state = load_state(state_path)
            baseline = json.loads(baseline_path.read_text())
            validate_baseline(baseline)
            call_tool = call_tool or runtime_adapter()
            snapshot = collect_snapshot(call_tool,dt.datetime.now(dt.timezone.utc))
            state,messages = evaluate(snapshot,state,baseline)
            save_state(state_path,state)
            emit(messages)
        return 0
    except Exception:
        print("Spamhaus monitor runtime failure: verify adapter, baseline, state, and single-run lock.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
