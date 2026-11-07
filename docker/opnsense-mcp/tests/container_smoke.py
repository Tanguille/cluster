"""Run from the test venv against a locally built image; no firewall networking."""
import asyncio
import sys
import subprocess
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    image = sys.argv[1] if len(sys.argv)>1 else "opnsense-mcp:local"
    invariant = '''from pathlib import Path
import opnsense_mcp.config as config
assert config.restricted_build() and config.monitor_enabled()
marker = Path(config.__file__).with_name("MONITOR_ONLY")
assert marker.stat().st_uid == 0 and marker.stat().st_mode & 0o777 == 0o444
try:
    config.OPNsenseConfig(url="https://firewall.invalid/api", api_key="synthetic",
                         api_secret="synthetic", verify_ssl=True, allow_writes=False,
                         api_style="snake_case", monitor_only=False)
except config.ConfigError:
    pass
else:
    raise AssertionError("Unrestricted construction accepted")
'''
    subprocess.run(["docker","run","--rm","--read-only","--network=none",
                    "-e","OPNSENSE_MONITOR_ONLY=false","--entrypoint","/venv/bin/python",
                    image,"-c",invariant],check=True)
    command = StdioServerParameters(command="docker", args=[
        "run", "--rm", "-i", "--read-only", "--network=none", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
        "-e", "OPNSENSE_URL=https://firewall.invalid/api",
        "-e", "OPNSENSE_MONITOR_ONLY=false",
        "-e", "OPNSENSE_API_KEY=synthetic-key", "-e", "OPNSENSE_API_SECRET=synthetic-secret", image])
    async with stdio_client(command) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            catalog = await session.list_tools()
            assert all(tool.description and "read" in tool.description.lower() for tool in catalog.tools)
            assert {t.name for t in catalog.tools} == {"opn_monitor_"+name for name in (
                "alias_config","alias_contents","table_metadata","rules","rule_stats","logs")}
            denied = await session.call_tool("opn_firewall_apply",{})
            assert denied.isError
            bounded = await session.call_tool("opn_monitor_alias_contents",{"page_size":501})
            assert bounded.isError
            unavailable = await session.call_tool("opn_monitor_alias_config",{})
            assert unavailable.isError
            assert "synthetic-secret" not in str(unavailable)
    print("Container stdio initialize/catalog/denied/bounded/unavailable calls passed")


if __name__ == "__main__":
    asyncio.run(main())
