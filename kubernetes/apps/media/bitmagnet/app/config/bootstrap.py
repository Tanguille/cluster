"""Install the Armiger runtime, connector and model into the PVC when their pins change."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(os.environ.get("ARMIGER_ROOT", "/data/armiger"))
REQUIREMENTS = Path(os.environ.get("ARMIGER_REQUIREMENTS", "/bootstrap/requirements.txt"))
TORCH = "torch==2.14.1+cpu"
# Commit-pinned, so the fetched content is immutable; Renovate bumps each pair.
CONNECTOR_REPO = "https://github.com/spencernorton/Armiger-Connectors"
CONNECTOR_REVISION = "6bcd1d593bc1aa3c72ff08aaaff8417d807a6fc3"
MODEL_REPO = "https://huggingface.co/NorviTech/armiger-ultra"
MODEL_REVISION = "18ef5418b3858ee4f70c137540febb5343b8754e"
RUNTIME = ROOT / "runtime"
MODEL = ROOT / "model"
PYTHON = RUNTIME / "venv/bin/python3"
SOURCE = RUNTIME / "bitmagnet_connector.py"
# A venv is tied to the python minor that built it.
RUNTIME_KEY = hashlib.sha256(
    f"{sys.version_info.major}.{sys.version_info.minor}\n{TORCH}\n".encode() + REQUIREMENTS.read_bytes()
).hexdigest()


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def ready(marker, key):
    return marker.exists() and marker.read_text() == key


# Earlier layouts.
for legacy in [*ROOT.glob("runtime-*"), *ROOT.glob("model-*")]:
    shutil.rmtree(legacy)

if not ready(RUNTIME / ".ready", RUNTIME_KEY):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / ".ready").unlink(missing_ok=True)
    run("python3", "-m", "venv", "--clear", RUNTIME / "venv")
    run(PYTHON, "-m", "pip", "install", "--no-cache-dir", "-r", REQUIREMENTS)
    run(PYTHON, "-m", "pip", "install", "--no-cache-dir", TORCH,
        "--index-url", "https://download.pytorch.org/whl/cpu")
    run(PYTHON, "-c", "import numpy, onnxruntime, cryptography, huggingface_hub, torch")
    (RUNTIME / ".ready").write_text(RUNTIME_KEY)

if not (SOURCE.exists() and ready(RUNTIME / ".source", CONNECTOR_REVISION)):
    url = f"{CONNECTOR_REPO}/raw/{CONNECTOR_REVISION}/bitmagnet/bitmagnet_connector.py"
    with urllib.request.urlopen(url, timeout=60) as response:
        SOURCE.with_suffix(".tmp").write_bytes(response.read())
    SOURCE.with_suffix(".tmp").replace(SOURCE)
    (RUNTIME / ".source").write_text(CONNECTOR_REVISION)

if not ready(MODEL / ".ready", MODEL_REVISION):
    shutil.rmtree(MODEL, ignore_errors=True)
    repo_id = MODEL_REPO.removeprefix("https://huggingface.co/")
    run(PYTHON, "-c",
        "from huggingface_hub import snapshot_download; "
        f"snapshot_download(repo_id='{repo_id}', revision='{MODEL_REVISION}', local_dir='{MODEL}')")
    (MODEL / ".ready").write_text(MODEL_REVISION)
