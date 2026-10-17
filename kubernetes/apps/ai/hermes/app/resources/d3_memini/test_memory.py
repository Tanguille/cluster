import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


class UpstreamProvider:
    def prefetch(self, query, **kwargs): return ("recall", query, kwargs)
    def sync_turn(self, user, assistant, **kwargs): return ("capture", user, assistant, kwargs)
    def handle_tool_call(self, *args, **kwargs): return (args, kwargs)
    def on_pre_compress(self, *args, **kwargs): return (args, kwargs)
    def on_memory_write(self, *args, **kwargs): return (args, kwargs)
    def initialize(self, *args, **kwargs): return (args, kwargs)
    def shutdown(self): return "shutdown"


class MemoryTest(unittest.TestCase):
    def setUp(self):
        upstream = ModuleType("cluster_upstream_memini")
        upstream.MeminiMemoryProvider = UpstreamProvider
        self.decisions = ModuleType("cluster_d3_decisions_client")
        self.decisions.decide = Mock(return_value={})
        spec = importlib.util.spec_from_file_location("d3_memini", Path(__file__).with_name("__init__.py"))
        self.plugin = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"cluster_upstream_memini": upstream, "cluster_d3_decisions_client": self.decisions}):
            spec.loader.exec_module(self.plugin)
        self.provider = self.plugin.D3MeminiMemoryProvider()

    def test_only_confident_skip_suppresses_automatic_memory(self):
        self.decisions.decide.return_value = {"recall": "skip"}
        self.assertEqual(self.provider.prefetch("hello", session_id="s"), "")
        self.assertEqual(self.provider._prefetch_n, 1)
        self.decisions.decide.return_value = {"capture": "skip"}
        self.assertIsNone(self.provider.sync_turn("hello", "hi", session_id="s"))

    def test_unknown_and_failure_delegate_full_original_inputs(self):
        for result in ({}, {"recall": "recall", "capture": "capture"}):
            self.decisions.decide.return_value = result
            self.assertEqual(self.provider.prefetch("query", session_id="s"), ("recall", "query", {"session_id": "s"}))
            self.assertEqual(self.provider.sync_turn("user", "assistant", session_id="s"),
                             ("capture", "user", "assistant", {"session_id": "s"}))
        self.decisions.decide.side_effect = RuntimeError("offline")
        self.assertEqual(self.provider.prefetch("query"), ("recall", "query", {}))
        self.assertEqual(self.provider.sync_turn("user", "assistant"), ("capture", "user", "assistant", {}))

    def test_explicit_and_lifecycle_methods_are_inherited(self):
        for method in ("handle_tool_call", "on_pre_compress", "on_memory_write", "initialize", "shutdown"):
            self.assertIs(getattr(self.plugin.D3MeminiMemoryProvider, method), getattr(UpstreamProvider, method))
        self.assertEqual(self.provider.handle_tool_call("memory_remember", {"content": "remember"}),
                         (("memory_remember", {"content": "remember"}), {}))
        self.decisions.decide.assert_not_called()

    def test_server_disabled_memory_does_not_classify(self):
        self.provider._recall_enabled = False
        self.provider._capture_enabled = False
        self.provider.prefetch("query")
        self.provider.sync_turn("user", "assistant")
        self.decisions.decide.assert_not_called()

    def test_registers_unique_native_memory_provider(self):
        ctx = Mock()
        self.plugin.register(ctx)
        provider = ctx.register_memory_provider.call_args.args[0]
        self.assertEqual(provider.name, "d3-memini")


if __name__ == "__main__": unittest.main()
