"""Synthetic 26.7.8 contract and fail-closed security tests; no firewall access."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from opnsense_mcp.config import load_config, ConfigError
from opnsense_mcp.api_client import OPNsenseAPI, OPNsenseAPIError


@pytest.fixture
def config(monkeypatch):
    for key, value in {
        "URL": "https://firewall.invalid/api", "API_KEY": "synthetic-key",
        "API_SECRET": "synthetic-secret", "MONITOR_ONLY": "true",
        "API_STYLE": "snake_case", "VERIFY_SSL": "true",
        "ALLOW_WRITES": "false", "ALLOW_SENSITIVE": "false",
    }.items():
        monkeypatch.setenv("OPNSENSE_" + key, value)
    return load_config()


def test_profile_exists(config):
    assert getattr(config, "monitor_only", False)


@pytest.mark.parametrize("key,value", [("ALLOW_WRITES", "true"), ("ALLOW_SENSITIVE", "true"), ("VERIFY_SSL", "false"), ("API_STYLE", "auto"), ("URL", "http://firewall.invalid/api")])
def test_startup_rejects_unsafe(config, monkeypatch, key, value):
    monkeypatch.setenv("OPNSENSE_" + key, value)
    with pytest.raises(ConfigError):
        load_config()


async def client(config, handler):
    api = OPNsenseAPI(config)
    await api._client.aclose()
    api._client = httpx.AsyncClient(base_url=config.url, transport=httpx.MockTransport(handler))
    return api


@pytest.mark.asyncio
async def test_page_and_no_firmware(config):
    seen = []
    def handle(req):
        seen.append(req)
        assert req.url.path == "/api/firewall/alias_util/list/Spamhaus_DROP"
        body = json.loads(req.content)
        return httpx.Response(200, json={"total": 1853, "current": body["current"], "rowCount": 1, "rows": [{"ip": "2001:db8::/32"}]})
    api = await client(config, handle)
    result = await api.request_monitor("contents")
    assert result["total"] == 1853
    assert result["schema_version"] == 1
    assert len(seen) == 1
    await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"page_size":501}, {"page":0}, {"limit":2001}, {"digest":"../unsafe"}])
async def test_bounds_before_http(config, kwargs):
    def forbidden(req):
        pytest.fail("HTTP must not occur")
    api = await client(config, forbidden)
    with pytest.raises(OPNsenseAPIError):
        await api.request_monitor("contents", **kwargs)
    await api.close()


@pytest.mark.asyncio
async def test_generic_access_denied_before_http(config):
    api = await client(config, lambda req: pytest.fail("HTTP must not occur"))
    for method, path in [("POST", "firewall/alias/refresh"), ("GET", "core/firmware/status"), ("GET", "firewall/alias/search_item")]:
        with pytest.raises(OPNsenseAPIError):
            await api._request(method, path)
    with pytest.raises(OPNsenseAPIError):
        await api.get_text("core.backup.download")
    with pytest.raises(OPNsenseAPIError):
        await api.request_monitor("unknown")
    await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [httpx.Response(302, headers={"location":"https://other.invalid"}), httpx.Response(403, text="synthetic-secret"), httpx.Response(200, text="<html>synthetic-secret</html>"), httpx.Response(200, content=b"x"*(8*1024*1024+1)), httpx.Response(200,json={"unexpected":True})])
async def test_sanitized_failures(config, response, caplog):
    api = await client(config, lambda req: response)
    with pytest.raises(OPNsenseAPIError) as error:
        await api.request_monitor("contents")
    assert "synthetic-secret" not in str(error.value) + caplog.text
    await api.close()


@pytest.mark.asyncio
async def test_stats_metadata_logs(config):
    responses = {
        "rule_stats": {"status":"ok", "stats":{"uuid":{"packets":42,"pf_rules":3}}},
        "list_rule_ids": {"items":[{"id":"uuid","descr":"synthetic"}]},
        "get_table_size": {"status":"ok", "details":{"Spamhaus_DROP":{"count":9999,"updated":"2001-01-01T00:00:00"},"private":{}},"size":100000,"used":9999},
        "log": [{"__timestamp__":"2026-11-07T10:00:00", "__digest__":"a"*32,"rid":"unknown", "dir":"out","interface":"pppoe0"}],
    }
    api = await client(config, lambda req: httpx.Response(200,json=responses[req.url.path.split("/")[-1]]))
    stats = await api.request_monitor("stats")
    assert stats["stats"]["uuid"] == {"packets":42,"pf_rules":3}
    meta = await api.request_monitor("metadata")
    assert meta["refresh_success"] == "unknown" and "private" not in json.dumps(meta)
    logs = await api.request_monitor("logs",limit=1)
    assert logs["saturated"] and logs["rows"][0]["rid"] == "unknown"
    assert logs["timestamp_coverage"] == "unknown"
    await api.close()


def test_only_six_tools(config):
    script = '''import asyncio
from opnsense_mcp.server import mcp
async def run():
    tools = await mcp.list_tools()
    assert sorted(t.name for t in tools) == sorted(["opn_monitor_"+s for s in ["alias_config","alias_contents","table_metadata","rules","rule_stats","logs"]])
asyncio.run(run())
'''
    subprocess.run([sys.executable,"-c",script],env=os.environ.copy(),check=True)


@pytest.mark.asyncio
async def test_extra_keys_and_alias_switch_denied(config):
    from opnsense_mcp.tools.monitor import read
    api = await client(config, lambda req: pytest.fail("HTTP must not occur"))
    for key,body,params in [("logs",None,{"limit":1,"url":"https://other.invalid"}), ("config",{"current":1,"rowCount":500,"searchPhrase":"other"},None), ("contents",{"current":1,"rowCount":500,"extra":1},None), ("metadata",None,{"extra":1})]:
        with pytest.raises(OPNsenseAPIError):
            await read(api,key,body,params)
    await api.close()


@pytest.mark.asyncio
async def test_missing_alias_differs_from_denied(config):
    api = await client(config,lambda req:httpx.Response(200,json={"rows":[],"total":0,"current":1,"rowCount":0}))
    assert (await api.request_monitor("config"))["alias"] is None
    await api.close()
    api = await client(config,lambda req:httpx.Response(403,json={"message":"private"}))
    with pytest.raises(OPNsenseAPIError) as caught:
        await api.request_monitor("config")
    assert caught.value.status_code == 403
    await api.close()


@pytest.mark.asyncio
async def test_source_shaped_fixture(config):
    fixture=json.loads((Path(__file__).parent/"fixtures/26.7.8.json").read_text())
    api = await client(config,lambda req:httpx.Response(200,json=fixture[req.url.path]))
    result=await api.request_monitor("config")
    assert result["alias"]["type"] == "urltable"
    assert result["alias"]["updatefreq"] == "1"
    result=await api.request_monitor("contents")
    assert result["rows"][1]["ip"] == "2001:db8::/32"
    await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status",[401,403,500])
async def test_http_failures_preserve_status_not_body(config,status):
    api=await client(config,lambda req:httpx.Response(status,text="synthetic-secret"))
    with pytest.raises(OPNsenseAPIError) as error:
        await api.request_monitor("logs")
    assert error.value.status_code==status
    assert "synthetic-secret" not in str(error.value)
    await api.close()


@pytest.mark.asyncio
async def test_transport_timeout_sanitized(config):
    def timeout(req):
        raise httpx.ReadTimeout("synthetic-secret",request=req)
    api=await client(config,timeout)
    with pytest.raises(OPNsenseAPIError) as error:
        await api.request_monitor("logs")
    assert "synthetic-secret" not in str(error.value)
    await api.close()


@pytest.mark.asyncio
async def test_explicit_style_never_probes_firmware(config):
    api=await client(config,lambda req:pytest.fail("firmware HTTP must not occur"))
    await api._ensure_version_detected()
    assert api._use_snake_case
    assert api.detected_version is None
    await api.close()


@pytest.mark.asyncio
async def test_public_generic_methods_deny_even_allowlisted_paths(config):
    api=await client(config,lambda req:pytest.fail("HTTP must not occur"))
    with pytest.raises(OPNsenseAPIError):
        await api.get("firewall.alias.search")
    with pytest.raises(OPNsenseAPIError):
        await api.post("firewall.alias.search",{"current":1,"rowCount":1})
    await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit",[0,-1,True,2001])
async def test_log_limit_rejected_before_http(config,limit):
    api=await client(config,lambda req:pytest.fail("HTTP must not occur"))
    with pytest.raises(OPNsenseAPIError):
        await api.request_monitor("logs",limit=limit)
    await api.close()


@pytest.mark.asyncio
async def test_max_log_limit_preserves_raw_sample_and_digest(config):
    rows=[{"__timestamp__":"2026-11-07T12:00:00+00:00","__digest__":f"{i:032x}","rid":"uuid","dir":"out","interface":"pppoe0"} for i in range(2000)]
    def handle(req):
        assert req.method=="GET"
        assert req.url.path=="/api/diagnostics/firewall/log"
        assert dict(req.url.params)=={"limit":"2000","digest":"a"*32}
        return httpx.Response(200,json=rows)
    api=await client(config,handle)
    result=await api.request_monitor("logs",limit=2000,digest="a"*32)
    assert result["raw_count"]==2000 and result["saturated"]
    assert result["rows"]==rows and result["digest"]==rows[0]["__digest__"]
    await api.close()


@pytest.mark.asyncio
async def test_all_seven_routes_use_verified_source_shapes(config):
    fixture=json.loads((Path(__file__).parent/"fixtures/26.7.8.json").read_text())
    seen=[]
    posts={"/api/firewall/alias/search_item","/api/firewall/alias_util/list/Spamhaus_DROP","/api/firewall/filter/search_rule"}
    def handle(req):
        assert req.url.path in fixture
        assert req.method==("POST" if req.url.path in posts else "GET")
        seen.append(req.url.path)
        return httpx.Response(200,json=fixture[req.url.path])
    api=await client(config,handle)
    for endpoint in ("config","contents","metadata","rules","stats","logs"):
        result=await api.request_monitor(endpoint,page_size=100 if endpoint=="rules" else 500)
        assert result["schema_version"]==1
        if endpoint=="rules":
            assert result["total"]==3 and result["rows"][1]["uuid"]==result["rows"][2]["uuid"]
        if endpoint=="logs":
            assert result["timestamp_coverage"]=="unknown"
    assert set(seen)==set(fixture) and len(seen)==7
    await api.close()


def test_restricted_build_cannot_be_disabled_by_environment(config,monkeypatch):
    from opnsense_mcp import config as module
    monkeypatch.setattr(module,"restricted_build",lambda:True)
    monkeypatch.setenv("OPNSENSE_MONITOR_ONLY","false")
    assert module.monitor_enabled() and module.load_config().monitor_only
    with pytest.raises(ConfigError):
        module.OPNsenseConfig(url=config.url,api_key="synthetic-key",api_secret="synthetic-secret",verify_ssl=True,allow_writes=False,api_style="snake_case",monitor_only=False)
