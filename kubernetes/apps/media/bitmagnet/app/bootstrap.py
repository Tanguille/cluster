"""Install the Armiger runtime and model into the PVC when their pins change."""
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
# Not tracked by Renovate.
REVISION = "fd5c3faae6f5bd915a66010b7d1ed67ed8dd96f8"
SOURCE_SHA256 = "99db79de2c70f1c9b8e0d41c4b441c106c5557ae08ab61af62db0abae51e776c"
MODEL_REVISION = "cdf7036c45a0d9751d18e738c7abfde485552964"
RUNTIME = ROOT / "runtime"
MODEL = ROOT / "model"
PYTHON = RUNTIME / "venv/bin/python3"
# A venv is tied to the python minor that built it.
RUNTIME_KEY = hashlib.sha256(
    f"{sys.version_info.major}.{sys.version_info.minor}\n{TORCH}\n".encode() + REQUIREMENTS.read_bytes()
).hexdigest()


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def ready(path, key):
    marker = path / ".ready"
    return marker.exists() and marker.read_text() == key


# Earlier layouts.
for legacy in [*ROOT.glob("runtime-*"), *ROOT.glob("model-*")]:
    shutil.rmtree(legacy)

if not ready(RUNTIME, RUNTIME_KEY):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / ".ready").unlink(missing_ok=True)
    run("python3", "-m", "venv", "--clear", RUNTIME / "venv")
    run(PYTHON, "-m", "pip", "install", "--no-cache-dir", "-r", REQUIREMENTS)
    run(PYTHON, "-m", "pip", "install", "--no-cache-dir", TORCH,
        "--index-url", "https://download.pytorch.org/whl/cpu")
    run(PYTHON, "-c", "import numpy, onnxruntime, cryptography, huggingface_hub, torch")
    (RUNTIME / ".ready").write_text(RUNTIME_KEY)

source = RUNTIME / "bitmagnet_connector.py"
if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA256:
    url = f"https://raw.githubusercontent.com/spencernorton/Armiger-Connectors/{REVISION}/bitmagnet/bitmagnet_connector.py"
    with urllib.request.urlopen(url, timeout=60) as response:
        content = response.read()
    if hashlib.sha256(content).hexdigest() != SOURCE_SHA256:
        raise RuntimeError("Connector source digest mismatch")
    temporary = source.with_suffix(".tmp")
    temporary.write_bytes(content)
    temporary.replace(source)

if not ready(MODEL, MODEL_REVISION):
    shutil.rmtree(MODEL, ignore_errors=True)
    run(PYTHON, "-c",
        "from huggingface_hub import snapshot_download; "
        f"snapshot_download(repo_id='NorviTech/armiger-ultra', revision='{MODEL_REVISION}', local_dir='{MODEL}')")
    (MODEL / ".ready").write_text(MODEL_REVISION)
