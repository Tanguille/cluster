import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


# In the pod d3_client.py is mounted under /opt/d3/shared; here it lives in the shared gateway component.
sys.path.insert(0, str(Path(__file__).parents[6] / "components/d3-gateway/resources"))
spec = importlib.util.spec_from_file_location("d3_decisions", Path(__file__).with_name("__init__.py"))
plugin = importlib.util.module_from_spec(spec)


def answer(question, choice):
    choices = plugin.QUESTIONS[question]["criteria"]
    return {"type": "choice", "choice": choice, "confidence": 0.95,
            "input_coverage": "complete", "probabilities": {
                key: 0.96 if key == choice else 0.04 / (len(choices) - 1) for key in choices}}


class Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, size): return self.body


class DecisionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec.loader.exec_module(plugin)

    def setUp(self):
        plugin._decisions.clear()

    def test_valid_answer_and_untrusted_results(self):
        valid = answer("thinking", "fast")
        self.assertEqual(plugin._choice("thinking", valid), "fast")
        for update in ({"confidence": 0.5}, {"input_coverage": "partial"},
                       {"choice": "external"}, {"probabilities": {"fast": float("nan")}},
                       {"probabilities": {"fast": True, "reasoning": 0, "other": 0}},
                       {"probabilities": {"fast": 0.4, "reasoning": 0.3, "other": 0.3}},
                       {"probabilities": {"fast": 0.96, "reasoning": 0.02}}):
            with self.subTest(update=update):
                self.assertIsNone(plugin._choice("thinking", {**valid, **update}))

    def test_native_payload_and_bounded_response(self):
        body = json.dumps({"model": "d3-nano", "answers": {"route": answer("route", "cluster")}}).encode()
        with patch.object(plugin._d3, "urlopen", return_value=Response(body)) as opened:
            self.assertEqual(plugin.decide("check cluster"), {"route": "cluster"})
            request = opened.call_args.args[0]
            payload = json.loads(request.data)
            self.assertEqual(payload["state"], "check cluster")
            self.assertTrue(all(q["require_full_input"] for q in payload["questions"].values()))
            self.assertEqual(opened.call_args.kwargs["timeout"], 3)

    def test_errors_and_oversize_preserve_workflow(self):
        with patch.object(plugin._d3, "urlopen", side_effect=TimeoutError):
            self.assertEqual(plugin.decide("check cluster"), {})
        with patch.object(plugin._d3, "urlopen") as opened:
            for content in ("", "x" * 4097, [{"type": "image_url"}], None):
                self.assertEqual(plugin.decide(content), {})
            opened.assert_not_called()

    def test_advice_and_local_route_reuse_turn_decision(self):
        with patch.object(plugin, "decide", return_value={"route": "home_assistant",
                "thinking": "fast"}) as classify:
            result = plugin.before_turn(user_message="turn on lights", session_id="s", turn_id="t")
            self.assertIn("home_assistant", result["context"])
            self.assertIn("permissions", result["context"])
            original = {"model": "qwen-3.8", "extra_body": {"other": True}}
            routed = plugin.route_request(request=original, session_id="s", turn_id="t", base_url="http://litellm.ai.svc.cluster.local/v1")["request"]
            self.assertEqual(routed["model"], "qwen-3.8-fast")
            self.assertFalse(routed["extra_body"]["chat_template_kwargs"]["enable_thinking"])
            self.assertEqual(original, {"model": "qwen-3.8", "extra_body": {"other": True}})
            classify.assert_called_once()

    def test_explicit_controls_external_alias_and_missing_turn_untouched(self):
        plugin._decisions[("s", "t")] = {"thinking": "fast"}
        for request in ({"model": "omniroute"}, {"model": "qwen-3.8", "reasoning_effort": "high"},
                        {"model": "qwen-3.8", "extra_body": {"chat_template_kwargs": {"enable_thinking": True}}},
                        {"model": "qwen-3.8", "extra_body": {"chat_template_kwargs": {"reasoning_effort": "xhigh"}}}):
            with self.subTest(request=request):
                self.assertIsNone(plugin.route_request(request=request, session_id="s", turn_id="t", base_url="http://litellm.ai.svc.cluster.local/v1"))
        self.assertIsNone(plugin.route_request(request={"model": "qwen-3.8"}, session_id="s", turn_id="other", base_url="http://litellm.ai.svc.cluster.local/v1"))

    def test_reasoning_restored_and_ambiguous_request_untouched(self):
        plugin._decisions[("s", "t")] = {"thinking": "reasoning"}
        request = {"model": "qwen-3.8-fast"}
        result = plugin.route_request(request=request, session_id="s", turn_id="t", base_url="http://litellm.ai.svc.cluster.local/v1/")["request"]
        self.assertEqual(result["model"], "qwen-3.8")
        self.assertTrue(result["extra_body"]["chat_template_kwargs"]["enable_thinking"])
        with patch.object(plugin, "decide", return_value={}):
            self.assertIsNone(plugin.before_turn(user_message="ambiguous", session_id="s", turn_id="t"))
        self.assertIsNone(plugin.route_request(request=request, session_id="s", turn_id="t", base_url="http://litellm.ai.svc.cluster.local/v1"))

    def test_bad_json_incomplete_and_oversized_responses_fallback(self):
        cases = [b"{", b"[]", b"x" * 65537,
                 json.dumps({"model": "other", "answers": {}}).encode(),
                 json.dumps({"model": "d3-nano", "answers": {"thinking": {
                     **answer("thinking", "fast"), "input_coverage": "partial"}}}).encode()]
        for body in cases:
            with self.subTest(body=body[:60]), patch.object(plugin._d3, "urlopen", return_value=Response(body)):
                self.assertEqual(plugin.decide("check cluster"), {})

    def test_registers_supported_interfaces(self):
        from unittest.mock import Mock
        ctx = Mock()
        plugin.register(ctx)
        ctx.register_hook.assert_any_call("pre_llm_call", plugin.before_turn)
        ctx.register_middleware.assert_called_once_with("llm_request", plugin.route_request)
        self.assertEqual(ctx.register_tool.call_count, 3)

    def test_explicit_classifiers_are_advisory_and_use_caller_categories(self):
        with patch.object(plugin, "decide", return_value={"intent": "status"}) as decide:
            result = json.loads(plugin.home_intent({"text": "how warm is it?"}))
            self.assertEqual(result["recommendations"], {"intent": "status"})
            self.assertTrue(result["advisory"])
            self.assertIn("control", decide.call_args.args[1]["intent"]["criteria"])
        with patch.object(plugin, "decide", return_value={"category": "technology"}) as decide:
            result = json.loads(plugin.bookmark_category({"text": "new CPU", "categories": ["technology", "finance"]}))
            self.assertEqual(result["recommendations"], {"category": "technology"})
            self.assertEqual(set(decide.call_args.args[1]["category"]["criteria"]), {"technology", "finance", "other"})
        with patch.object(plugin, "decide") as decide:
            for args in ({"text": "x", "categories": ["a"]}, {"text": "x", "categories": ["a", "a"]},
                         {"text": "x", "categories": ["a", "b"], "unexpected": True}):
                self.assertTrue(json.loads(plugin.bookmark_category(args))["fallback"])
            decide.assert_not_called()

    def test_native_url_override(self):
        response = Response(b'{"model":"d3-nano","answers":{}}')
        with patch.dict(plugin.os.environ, {"D3_DECISION_URL": "http://d3:8100/v1/systemone"}), patch.object(plugin._d3, "urlopen", return_value=response) as opened:
            plugin.decide("test")
            request = opened.call_args.args[0]
            self.assertEqual(request.full_url, "http://d3:8100/v1/systemone")

    def test_same_alias_on_external_or_unknown_endpoint_is_never_routed(self):
        plugin._decisions[("s", "t")] = {"thinking": "fast"}
        for base_url in (None, "", "https://external.example/v1", "http://litellm.ai.svc.cluster.local.evil/v1"):
            with self.subTest(base_url=base_url):
                self.assertIsNone(plugin.route_request(request={"model": "qwen-3.8"}, session_id="s", turn_id="t", base_url=base_url))


if __name__ == "__main__":
    unittest.main()
