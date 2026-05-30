#!/usr/bin/env python3
"""Search GitHub repositories tagged k8s-at-home.

Uses the GitHub REST search API with optional GH_TOKEN/GITHUB_TOKEN for higher
rate limits. Outputs a compact Markdown or JSON shortlist for further code
search and manual inspection.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request


def github_get(url: str) -> dict:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "k8s-at-home-research-skill",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - user-provided GitHub URL only
        return json.loads(resp.read().decode("utf-8"))


def search_repos(term: str, limit: int, sort: str) -> list[dict]:
    query = "topic:k8s-at-home"
    if term:
        query += f" {term}"
    params = urllib.parse.urlencode({"q": query, "sort": sort, "order": "desc", "per_page": min(limit, 100)})
    data = github_get(f"https://api.github.com/search/repositories?{params}")
    return data.get("items", [])[:limit]


def render_markdown(items: list[dict], term: str) -> str:
    title = f"# k8s-at-home repositories for `{term}`" if term else "# k8s-at-home repositories"
    lines = [title, ""]
    for idx, repo in enumerate(items, 1):
        pushed = repo.get("pushed_at", "unknown")
        desc = (repo.get("description") or "").strip()
        topics = ", ".join(repo.get("topics", [])[:8])
        lines.extend(
            [
                f"{idx}. [{repo['full_name']}]({repo['html_url']})",
                f"   - stars: {repo.get('stargazers_count', 0)}; updated: {pushed}",
                f"   - description: {desc or 'n/a'}",
                f"   - topics: {topics or 'n/a'}",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Search GitHub repos tagged k8s-at-home")
    parser.add_argument("term", nargs="?", default="", help="optional app/chart/search term")
    parser.add_argument("--limit", type=int, default=10, help="number of repositories to return")
    parser.add_argument("--sort", choices=["stars", "updated"], default="stars")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    args = parser.parse_args()

    try:
        items = search_repos(args.term, args.limit, args.sort)
    except Exception as exc:  # noqa: BLE001 - CLI should surface API/rate-limit failures clearly
        print(f"GitHub search failed: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(items, indent=2))
    else:
        print(render_markdown(items, args.term))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
