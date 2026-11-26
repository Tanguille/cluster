import json
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "kubernetes/apps/ai/hermes/app/resources/orbit_report.py"


class OrbitReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_collects_open_tasks_with_the_latest_replies_of_tagged_tasks(self):
        task = dict(id="a", identifier="T-1", project_id="p", status_id="open", title="[Hermes] Decision",
                    description="Which option?", created_at="2026-11-01", updated_at="2026-11-04", completed_at=None)
        other = dict(task, id="b", identifier="T-2", title="Check the schedule", description="For Hermes")
        done = dict(task, id="c", identifier="T-3", completed_at="2026-11-02")
        replies = [{"body": f"Reply {day}", "created_at": f"2026-11-{day:02}"} for day in range(9, 2, -1)]
        pages = [
            ("orbit_list_tasks", {}, [task, done], "next"),
            ("orbit_list_tasks", {"cursor": "next"}, [other], None),
            ("orbit_list_task_statuses", {"project_id": "p"}, [{"id": "open", "name": "Open"}], None),
            ("orbit_list_comments", {"task_id": "a"}, replies, None),
        ]

        async def dispatch(name, call):
            tool, params, items, cursor = pages.pop(0)
            self.assertEqual(name, "call_tool")
            self.assertEqual(call, {"tool_name": tool, "parameters": params})
            body = json.dumps({"items": items, "next_cursor": cursor})
            return SimpleNamespace(content=[SimpleNamespace(text=body)])

        tagged, untagged = await runpy.run_path(str(SCRIPT))["collect"](SimpleNamespace(call_tool=dispatch))
        self.assertFalse(pages)
        self.assertEqual(tagged["status"], "Open")
        self.assertEqual([c["body"] for c in tagged["comments"]], [f"Reply {day}" for day in range(5, 10)])
        self.assertEqual(tagged["older_replies_omitted"], 2)
        self.assertTrue(untagged["mentions_hermes"])
        self.assertNotIn("description", untagged)
