"""Install declarative D3 plugins without replacing unrelated Hermes settings."""

import copy
import os
from pathlib import Path
import shutil
import site
import tempfile


def update_config(config):
    config = copy.deepcopy(config)

    def replace_models(value):
        if isinstance(value, dict):
            for key, item in value.items():
                value[key] = replace_models(item)
            return value
        if isinstance(value, list):
            for index, item in enumerate(value):
                value[index] = replace_models(item)
            return value
        return "qwen-3.8-fast" if isinstance(value, str) and value.lower() in {"qwen35-2b", "qwen3.5-2b"} else value

    config = replace_models(config)
    plugins = config.setdefault("plugins", {})
    enabled = plugins.get("enabled", [])
    if not isinstance(enabled, list):
        raise ValueError("plugins.enabled must be a list")
    plugins["enabled"] = [name for name in enabled if name not in {"qwen38-effort-router", "d3-decisions"}] + ["d3-decisions"]
    if isinstance(config.get("toolsets"), list) and "d3_decisions" not in config["toolsets"]:
        config["toolsets"].append("d3_decisions")
    if config.get("memory", {}).get("provider") == "memini":
        config["memory"]["provider"] = "d3-memini"
    return config


def configure(home, plugin_root, yaml):
    path = home / "config.yaml"
    if not path.is_file():
        # Let the image initialize an empty PVC; a later restart enables these plugins.
        print(f"No config at {path}; preserving native first-start bootstrap.")
        return False
    original = yaml.load(path)
    if not isinstance(original, dict):
        raise ValueError(f"Expected a configuration mapping: {path}")
    updated = update_config(original)
    for name in ("d3-decisions", "d3-memini"):
        source = plugin_root / name
        if not (source / "__init__.py").is_file():
            raise ValueError(f"Missing mounted plugin: {name}")
        link = home / "plugins" / name
        link.parent.mkdir(exist_ok=True)
        if link.is_symlink() and link.resolve() == source.resolve():
            continue
        if link.exists() or link.is_symlink():
            raise ValueError(f"Refusing to replace a user plugin: {link}")
        link.symlink_to(source, target_is_directory=True)
    if updated == original:
        return True
    backup = path.with_suffix(".yaml.before-d3")
    if not backup.exists():
        shutil.copy2(path, backup)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=home, delete=False) as output:
            temporary = Path(output.name)
            yaml.dump(updated, output)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def main():
    # The Hermes launcher adds its managed site directory; init containers bypass it.
    for directory in Path("/opt/hermes/.venv/lib").glob("python*/site-packages"):
        site.addsitedir(str(directory))
    from ruamel.yaml import YAML

    yaml = YAML()
    root = Path("/opt/data")
    homes = [root, *(path.parent for path in (root / "profiles").glob("*/config.yaml"))]
    configured = sum(configure(home, Path("/opt/d3/plugins"), yaml) for home in homes)
    print(f"Configured D3 plugins in {configured} Hermes profiles; prior settings backed up on the PVC.")


if __name__ == "__main__":
    main()
