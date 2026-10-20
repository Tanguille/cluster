import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("configure_d3", Path(__file__).with_name("configure_d3.py"))
configure_d3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure_d3)


class ConfigureTest(unittest.TestCase):
    def test_replaces_old_router_and_models_without_enabling_memory(self):
        config = {"plugins": {"enabled": ["homeassistant", "qwen38-effort-router"]}, "auxiliary": {"title_generation": {"model": "qwen35-2b"}}, "unrelated": "keep me"}
        updated = configure_d3.update_config(config)
        self.assertEqual(updated["plugins"]["enabled"], ["homeassistant", "d3-decisions"])
        self.assertEqual(updated["auxiliary"]["title_generation"]["model"], "qwen-3.8-fast")
        self.assertNotIn("memory", updated)
        self.assertEqual(updated["unrelated"], "keep me")
        self.assertEqual(configure_d3.update_config(updated), updated)

    def test_wraps_existing_memini_only(self):
        config = {"memory": {"provider": "memini", "enabled": True}, "model": {"default": "qwen3.5-2b"}}
        result = configure_d3.update_config(config)
        self.assertEqual(result["memory"], {"provider": "d3-memini", "enabled": True})
        self.assertEqual(result["model"]["default"], "qwen-3.8-fast")

    def test_leaves_another_provider_and_explicit_reasoning_alone(self):
        config = {"memory": {"provider": "file"}, "model": {"default": "qwen-3.8"}, "reasoning": {"effort": "high"}}
        result = configure_d3.update_config(config)
        self.assertEqual(result["memory"], config["memory"])
        self.assertEqual(result["model"], config["model"])
        self.assertEqual(result["reasoning"], config["reasoning"])

    def test_atomic_profile_migration_preserves_backup_permissions_and_comments(self):
        from ruamel.yaml import YAML
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            home.mkdir()
            plugins = Path(directory) / "mounted"
            for name in ("d3-decisions", "d3-memini"):
                (plugins / name).mkdir(parents=True)
                (plugins / name / "__init__.py").write_text("")
            path = home / "config.yaml"
            original = "# Keep this comment\nmemory:\n  provider: memini\nauxiliary:\n  title_generation:\n    model: qwen35-2b\n"
            path.write_text(original)
            path.chmod(0o600)
            configure_d3.configure(home, plugins, YAML())
            self.assertEqual(path.with_suffix(".yaml.before-d3").read_text(), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn("# Keep this comment", path.read_text())
            self.assertNotIn("qwen35-2b", path.read_text())
            self.assertEqual((home / "plugins/d3-decisions").resolve(), (plugins / "d3-decisions").resolve())
            migrated = path.read_bytes()
            configure_d3.configure(home, plugins, YAML())
            self.assertEqual(path.read_bytes(), migrated)
            self.assertEqual(path.with_suffix(".yaml.before-d3").read_text(), original)

    def test_does_not_replace_an_existing_user_plugin(self):
        from ruamel.yaml import YAML
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            (home / "plugins/d3-decisions").mkdir(parents=True)
            path = home / "config.yaml"
            path.write_text("plugins:\n  enabled: []\n")
            mounted = Path(directory) / "mounted/d3-decisions"
            mounted.mkdir(parents=True)
            (mounted / "__init__.py").write_text("")
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "user plugin"):
                configure_d3.configure(home, mounted.parent, YAML())
            self.assertEqual(path.read_bytes(), before)

    def test_missing_config_allows_native_first_start_bootstrap(self):
        from ruamel.yaml import YAML
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            configure_d3.configure(home, home / "mounted", YAML())
            self.assertFalse((home / "config.yaml").exists())


if __name__ == "__main__":
    unittest.main()
