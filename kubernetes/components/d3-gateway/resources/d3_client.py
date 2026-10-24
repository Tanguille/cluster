"""D3 /v1/systemone client pieces shared by the gateway and the Hermes plugins."""
import json
import math
from urllib.request import Request, urlopen

ALERT_CATEGORIES = {"capacity": "Resource capacity", "availability": "Service unavailable", "security": "Security concern",
                    "configuration": "Configuration or reconciliation", "other": "Other"}
ALERT_URGENCY = {"immediate": "Needs attention now", "soon": "Needs attention soon", "routine": "Routine attention"}


def question(instructions, criteria):
    return {"type": "choice", "instructions": instructions, "criteria": criteria, "require_full_input": True}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def post(url, state, questions, timeout, limit):
    request = Request(url, data=json.dumps({"state": state, "questions": questions}).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Oversized native response")
    return json.loads(raw)


def check_answer(answer, criteria, tolerance):
    """Raise unless answer is a complete choice over exactly these criteria whose probabilities sum to one."""
    if not isinstance(answer, dict) or answer.get("type") != "choice" or answer.get("input_coverage") != "complete" or "error" in answer:
        raise ValueError("Incomplete or failed decision")
    probabilities = answer.get("probabilities")
    if (not isinstance(probabilities, dict) or set(probabilities) != set(criteria)
            or any(not finite(p) or not 0 <= p <= 1 for p in probabilities.values())
            or not math.isclose(sum(probabilities.values()), 1, abs_tol=tolerance)):
        raise ValueError("Invalid probabilities")
    choice = answer.get("choice")
    if choice not in probabilities or probabilities[choice] != max(probabilities.values()):
        raise ValueError("Invalid choice")
