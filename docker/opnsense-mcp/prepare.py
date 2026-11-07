"""Verify source bytes and apply exact patches; runs only in the build stage."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import urllib.request

root = Path(__file__).parent
lock = json.loads((root / "upstream.lock.json").read_text())
archive = Path("/tmp/upstream.tar.gz")
urllib.request.urlretrieve(lock["archive_url"], archive)
if hashlib.sha256(archive.read_bytes()).hexdigest() != lock["archive_sha256"]:
    raise SystemExit("Source archive digest mismatch")
with tarfile.open(archive) as source:
    source.extractall("/tmp/source", filter="data")
source_root = Path("/tmp/source") / ("opnsense-mcp-server-" + lock["commit"])
source_root.rename("/src")
for patch in sorted((root / "patches").glob("*.patch")):
    subprocess.run(["git", "apply", "--check", str(patch)], cwd="/src", check=True)
    subprocess.run(["git", "apply", str(patch)], cwd="/src", check=True)
