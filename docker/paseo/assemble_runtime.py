"""Bind the sealed Hermes venv to the pinned source tree and write launchers.

This mirrors ``docker/build_agent.py`` from the pinned Hermes source. The only
difference is that no TUI or web frontend is assembled: this image runs Hermes
as an ACP server only, so the frontends are intentionally omitted.

Run from the image build as::

    PYTHONPATH=/opt/hermes /opt/hermes/.venv/bin/python /tmp/assemble_runtime.py
"""
from __future__ import annotations

from pathlib import Path
import sysconfig

from pm.store import current_target
from scripts.build.agent import assemble
from scripts.build.inputs import RESOURCE_ENV, AgentInputs

ROOT = Path("/opt/hermes")


def main() -> None:
    environment = ROOT / ".venv"
    site = Path(
        sysconfig.get_path(
            "purelib", vars={"base": str(environment), "platbase": str(environment)}
        )
    )
    resources = {name: ROOT / name for name in RESOURCE_ENV if (ROOT / name).is_dir()}
    manifest = assemble(
        AgentInputs(
            project=ROOT / "pyproject.toml",
            code=ROOT,
            repo=".",
            placement="fixed",
            target=current_target(),
            python=(environment / "bin/python").absolute(),
            site_packages=site,
            environment=environment,
            tools=ROOT / "tools",
            pm_runtime=ROOT / "pm-runtime",
            bin_dir="libexec",
            resources=resources,
            frontends={},
            stamp=ROOT / "install-stamp.json",
        ),
        ROOT,
    )
    # Preserve the venv command paths used by PATH and the entrypoint shim.
    for name, command in manifest["runtime"]["commands"].items():
        link = environment / "bin" / name
        link.unlink(missing_ok=True)
        link.symlink_to(f"../../{command}")


if __name__ == "__main__":
    main()
