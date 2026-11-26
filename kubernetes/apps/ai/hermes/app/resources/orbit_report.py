"""Collect the daily report's inputs through the existing ToolHive gateway."""

import asyncio
import json

# Hermes pastes this output into the prompt. The report only covers open tasks, so completed ones and
# older replies stay out; the input then grows with open work, not with Orbit's history.
LATEST_REPLIES = 5


async def read_pages(client, tool, **params):
    items = []
    while True:
        result = await client.call_tool("call_tool", {"tool_name": tool, "parameters": params})
        page = json.loads(result.content[0].text)
        items.extend(page["items"])
        if not page["next_cursor"]:
            return items
        params["cursor"] = page["next_cursor"]


async def collect(client):
    tasks = [task for task in await read_pages(client, "orbit_list_tasks") if not task["completed_at"]]
    statuses = {}
    for project in sorted({task["project_id"] for task in tasks}):
        for status in await read_pages(client, "orbit_list_task_statuses", project_id=project):
            statuses[status["id"]] = status["name"]

    report = []
    for task in tasks:
        row = {key: task[key] for key in ("identifier", "title", "created_at", "updated_at")}
        row["status"] = statuses[task["status_id"]]
        if "[hermes]" in task["title"].casefold():
            row["description"] = task["description"]
            comments = sorted(await read_pages(client, "orbit_list_comments", task_id=task["id"]), key=lambda c: c["created_at"])
            row["comments"] = [{key: c[key] for key in ("created_at", "body")} for c in comments[-LATEST_REPLIES:]]
            if len(comments) > LATEST_REPLIES:
                row["older_replies_omitted"] = len(comments) - LATEST_REPLIES
        elif "hermes" in task["description"].casefold():
            row["mentions_hermes"] = True
        report.append(row)
    return report


async def main():
    from mcp.client.client import Client

    async with Client("http://vmcp-unified.ai.svc.cluster.local:4483/mcp", raise_exceptions=True) as client:
        print(json.dumps(await collect(client), ensure_ascii=False))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        # Hermes includes script errors in the report. Keep upstream task data out of them.
        raise SystemExit(f"Orbit report input unavailable ({type(error).__name__})") from None
