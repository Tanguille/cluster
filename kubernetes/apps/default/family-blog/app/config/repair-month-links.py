#!/usr/bin/env python3
"""Repair monthly archive links in a staged static site, without changing other bytes."""

import argparse
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import sys


MONTH = re.compile(r"\s*(\d{2}-\d{4})\s*")

def href_value_span(tag):
    """Return the sole actual href value span; duplicate hrefs are ambiguous."""
    i = 2  # after '<a'
    attrs = []
    while i < len(tag):
        while i < len(tag) and (tag[i].isspace() or tag[i] == "/"):
            i += 1
        if i >= len(tag) or tag[i] == ">":
            break
        start = i
        while i < len(tag) and not tag[i].isspace() and tag[i] not in "=/>":
            i += 1
        if i == start:
            i += 1
            continue
        name = tag[start:i].lower()
        while i < len(tag) and tag[i].isspace():
            i += 1
        if i >= len(tag) or tag[i] != "=":
            continue
        i += 1
        while i < len(tag) and tag[i].isspace():
            i += 1
        if i >= len(tag):
            break
        if tag[i] in "\"'":
            quote = tag[i]
            i += 1
            value_start = i
            while i < len(tag) and tag[i] != quote:
                i += 1
            value_end = i
            i += i < len(tag)
        else:
            value_start = i
            while i < len(tag) and not tag[i].isspace() and tag[i] != ">":
                i += 1
            value_end = i
        if name == "href":
            attrs.append((value_start, value_end))
    return attrs[0] if len(attrs) == 1 else None


class LinkParser(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.text = text
        self.lines = [0]
        self.lines.extend(m.end() for m in re.finditer("\n", text))
        self.anchors = []
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        line, col = self.getpos()
        start = self.lines[line - 1] + col
        raw = self.get_starttag_text()
        span = href_value_span(raw)
        self.anchor = {"start": start, "raw": raw, "span": span, "label": []}

    def handle_data(self, data):
        if self.anchor is not None:
            self.anchor["label"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor is not None:
            self.anchors.append(self.anchor)
            self.anchor = None


def parse_page(path, root, months):
    text = path.read_bytes().decode("latin-1")
    parser = LinkParser(text)
    parser.feed(text)
    patches = []
    for a in parser.anchors:
        label = "".join(a["label"])
        found = MONTH.fullmatch(label)
        span = a["span"]
        if not found or not span:
            continue
        month = found.group(1)
        old = html.unescape(a["raw"][span[0]:span[1]])
        if old and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", old) and not old.startswith(("//", "#", "?")) and os.path.abspath(path.parent / old) == os.path.abspath(root / "index.html"):
            target = months.get(month)
            if target is None:
                raise ValueError("missing month target")
            new = os.path.relpath(target, path.parent).replace(os.sep, "/")
            if new != old:
                value_start = a["start"] + span[0]
                value_end = a["start"] + span[1]
                patches.append((value_start, value_end, new))
    for start, end, value in reversed(patches):
        text = text[:start] + value + text[end:]
    return text.encode("latin-1"), len(patches)


def atomic_write(path, data):
    """Replace path with data, keeping its mode and owner."""
    info = path.stat()
    fd, stage = tempfile.mkstemp(prefix=".month-link-repair-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.chmod(stage, stat.S_IMODE(info.st_mode))
        os.chown(stage, info.st_uid, info.st_gid)
        os.replace(stage, path)
    finally:
        if os.path.exists(stage):
            os.unlink(stage)


def main():
    arg = argparse.ArgumentParser()
    arg.add_argument("--site", default="/srv/site")
    arg.add_argument("--apply", action="store_true")
    args = arg.parse_args()
    root = Path(args.site)
    try:
        if not root.is_dir() or root.is_symlink():
            raise ValueError("invalid site")
        root = root.resolve(strict=True)
        marker = root.parent / ".ready"
        if not marker.is_file() or marker.is_symlink():
            raise ValueError("invalid readiness marker")
        marker_data = json.loads(marker.read_text(encoding="utf-8"))
        if (not isinstance(marker_data, dict)
                or type(marker_data.get("files")) is not int or marker_data["files"] < 0
                or type(marker_data.get("bytes")) is not int or marker_data["bytes"] < 0):
            raise ValueError("invalid readiness marker")
        pages = []
        for p in root.rglob("*"):
            if p.is_symlink():
                raise ValueError("symlink in site")
            if p.is_file() and p.suffix.lower() == ".html":
                pages.append(p)
        index = root / "index.html"
        if not index.is_file():
            raise ValueError("missing index")
        months = {}
        month_dir = root / "months"
        if month_dir.is_dir():
            for p in month_dir.iterdir():
                m = re.fullmatch(r"(\d{2}-\d{4})\.html", p.name)
                if m and p.is_file():
                    months[m.group(1)] = p
        plans = []
        total = 0
        for p in pages:
            new, count = parse_page(p, root, months)
            if count:
                plans.append((p, new))
                total += count
        if args.apply:
            for p, new in plans:
                atomic_write(p, new)
            files = [p for p in root.rglob("*") if p.is_file()]
            marker_data.update({"files": len(files), "bytes": sum(p.stat().st_size for p in files)})
            atomic_write(marker, json.dumps(marker_data, separators=(",", ":")).encode())
        print(json.dumps({"html_files": len(pages), "links": total, "changed_files": len(plans), "applied": args.apply}))
        return 0
    except (OSError, ValueError):  # covers JSONDecodeError and UnicodeError
        print("repair refused; site validation failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
