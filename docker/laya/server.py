"""Laya triage server: small, fast classifiers for ops events.

POST /v1/triage {"message": "..."} (questions optional) -> laya answers
(choice / score / yes-no with calibrated probabilities) in one forward pass,
a few seconds on CPU. The model is baked into the image and loaded from
LAYA_MODEL_DIR at startup, so the pod runs fully offline (HF_HUB_OFFLINE).
"""

import os
import threading

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import laya

MODEL_DIR = os.environ.get("LAYA_MODEL_DIR", "/model")

# Default triage question set: verified against real cluster events in the
# 2026-09-20 PoC (docs/llm-hosting/laya-triage.md). A caller may override it
# with its own question dict, same schema as laya's.
DEFAULT_QUESTIONS = {
    "is_genuine_anomaly": {
        "type": "noul",
        "instructions": (
            "Does this monitoring event indicate a genuine infrastructure anomaly "
            "requiring attention, versus a false positive, rate-limit, cosmetic, "
            "or already-handled signal?"
        ),
        "criteria": {
            "true": "genuine anomaly: real outage, corruption, leak, regression, failed job",
            "false": "false positive, transient rate limit, noise, informational, already-handled",
        },
    },
    "urgency": {
        "type": "score",
        "instructions": "How urgent is this infrastructure event, if acted on now?",
        "criteria": [
            "informational, no action needed",
            "monitor / investigate at leisure",
            "investigate today",
            "urgent, act now",
        ],
    },
    "category": {
        "type": "choice",
        "instructions": "Which subsystem does this event concern?",
        "criteria": {
            "llm_serving": "vLLM/SGLang/llama.cpp inference services, GPU, models",
            "storage": "Ceph, OSD, RBD, PVC, filesystem",
            "network": "OPNsense, firewall, interface, routing, DNS",
            "node": "Talos node health, kernel, reboot, memory, disk",
            "k8s": "Flux, Helm, pods, controllers, upgrades",
            "other": "none of the above",
        },
    },
}


class TriageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=16000)
    questions: dict | None = None


app = FastAPI(title="laya-triage", docs_url=None, redoc_url=None)
_router: "laya.Router" | None = None  # type: ignore[name-defined]
_lock = threading.Lock()


def get_router() -> laya.Router:  # type: ignore[name-defined]  # lazy __init__, runtime-verified
    global _router
    if _router is None:
        _router = laya.Router(
            preload=True,
            device="cpu",
            models={"english": (MODEL_DIR, None)},
        )
    return _router


@app.get("/healthz")
def healthz():
    if _router is None:
        raise HTTPException(status_code=503, detail="model loading")
    return {"status": "ok"}


@app.post("/v1/triage")
def triage(req: TriageRequest):
    questions = req.questions or DEFAULT_QUESTIONS
    # One forward pass per request; transformers models are not thread-safe,
    # so serialize inference. Bursts from cron jobs queue, they don't corrupt.
    with _lock:
        try:
            return get_router().system_one(
                {"message": req.message}, questions, model="english"
            )
        except Exception as exc:  # noqa: BLE001 - surface the reason to the caller
            raise HTTPException(status_code=500, detail=str(exc)) from exc
