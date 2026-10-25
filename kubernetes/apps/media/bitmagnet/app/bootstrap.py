"""Versioned bootstrap: preserve existing data and only mark successful installs."""
import hashlib
import os
from pathlib import Path
import subprocess
import urllib.request

# Both dirs come from the HelmRelease, shared with the app container.
RUNTIME = Path(os.environ["ARMIGER_RUNTIME_DIR"])
MODEL = Path(os.environ["ARMIGER_MODEL_DIR"])
MODEL_REVISION = MODEL.name.removeprefix("model-")
REVISION = "fd5c3faae6f5bd915a66010b7d1ed67ed8dd96f8"
SOURCE_SHA256 = "99db79de2c70f1c9b8e0d41c4b441c106c5557ae08ab61af62db0abae51e776c"
PYTHON = RUNTIME / "venv/bin/python3"


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


RUNTIME.mkdir(parents=True, exist_ok=True)
if not (RUNTIME / ".ready").exists():
    run("python3", "-m", "venv", RUNTIME / "venv")
    run(PYTHON, "-m", "pip", "install", "--no-cache-dir", "-r", "/bootstrap/requirements.txt")
    run(PYTHON, "-m", "pip", "install", "--no-cache-dir", "torch==2.14.1+cpu",
        "--index-url", "https://download.pytorch.org/whl/cpu")
    # A failed import must not produce the completion marker.
    run(PYTHON, "-c", "import numpy, onnxruntime, cryptography, huggingface_hub, torch")
    (RUNTIME / ".ready").touch()

# Check the digest on every start, not just existence of an old downloaded script.
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

if not (MODEL / ".ready").exists():
    run(PYTHON, "-c",
        "from huggingface_hub import snapshot_download; "
        f"snapshot_download(repo_id='NorviTech/armiger-ultra', revision='{MODEL_REVISION}', local_dir='{MODEL}')")
    (MODEL / ".ready").touch()
