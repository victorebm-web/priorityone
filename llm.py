"""Optional Claude integration: rewrite drafts and re-extract call-note risks, under guardrails.

Off by default. It switches on only when the `anthropic` SDK is installed and credentials are
available (ANTHROPIC_API_KEY, or a profile from `ant auth login`), and PRIORITYONE_LLM is not "off".
Every call is checked by the guardrails below; anything that fails falls back to the template,
and every call is written to the llm_calls audit table.
"""
import importlib.util
import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from models import LlmCall

MODEL = "claude-opus-5-5"
DRAFT_PROMPT_VERSION = "draft-v1"
EXTRACT_PROMPT_VERSION = "extract-v1"
MAX_DRAFT_CHARS = 1800

# Audience rules: words a customer-facing message must never contain.
CUSTOMER_BANNED = ["score", "prioritis", "prioritiz", "priority list", "ranking", "ranked", "acquirer",
                   "competitor", "churn", "at risk", "flagged", "priorityone", "internal", "gong", "signal"]
# No commercial commitments from generated text, for any audience.
COMMITMENT_TERMS = ["discount", "% off", "guarantee", "sla", "free of charge", "waive", "service credit",
                    "roadmap commitment", "we will refund"]

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set:
    return {m.replace(",", "").rstrip(".") for m in _NUMBER.findall(text)}


# --- Guardrails (pure, unit-tested) ------------------------------------------------------------

def check_draft(output: str, source_text: str, audience: str) -> List[str]:
    """Return a list of violations. An empty list means the draft may be shown."""
    problems = []
    low = output.lower()
    invented = sorted(_numbers(output) - _numbers(source_text))
    if invented:
        problems.append(f"numbers not in the source: {', '.join(invented[:5])}")
    if audience == "customer":
        hits = [w for w in CUSTOMER_BANNED if w in low]
        if hits:
            problems.append(f"internal language in a customer draft: {', '.join(hits)}")
    hits = [w for w in COMMITMENT_TERMS if w in low]
    if hits:
        problems.append(f"commercial commitment: {', '.join(hits)}")
    if len(output) > MAX_DRAFT_CHARS:
        problems.append("too long")
    if not output.strip():
        problems.append("empty")
    return problems


def check_extraction(data, note_text: str) -> List[str]:
    """Extracted risks/opportunities must be short lists of strings."""
    problems = []
    if not isinstance(data, dict):
        return ["not an object"]
    for key in ("risks", "opportunities"):
        items = data.get(key)
        if not isinstance(items, list) or len(items) > 4 or not all(isinstance(i, str) and 0 < len(i) <= 80 for i in items):
            problems.append(f"{key} must be up to 4 short strings")
    invented = _numbers(json.dumps(data)) - _numbers(note_text)
    if invented:
        problems.append(f"numbers not in the note: {', '.join(sorted(invented))}")
    return problems


# --- Client --------------------------------------------------------------------------------------

def status() -> Tuple[bool, str]:
    """(enabled, human-readable reason)."""
    if os.environ.get("PRIORITYONE_LLM", "").lower() == "off":
        return False, "Turned off with PRIORITYONE_LLM=off."
    if importlib.util.find_spec("anthropic") is None:
        return False, "Install the SDK (pip install anthropic) and set ANTHROPIC_API_KEY to enable."
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or os.environ.get("ANTHROPIC_PROFILE")):
        return False, "Set ANTHROPIC_API_KEY (or sign in with `ant auth login`) to enable."
    return True, f"Using {MODEL}. Drafts are checked by guardrails before they are shown."


def _call(system: str, user: str, output_format: Optional[dict] = None) -> Tuple[str, Optional[str]]:
    """One Messages API request. Returns (text, request_id). Raises on API errors."""
    import anthropic

    client = anthropic.Anthropic()
    kwargs = dict(
        model=MODEL, max_tokens=4000, system=system, messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        # Safety classifiers can decline; let the API re-run on the recommended fallback model.
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
    )
    config = {"effort": "low"}  # short rewriting/extraction: low effort is enough
    if output_format:
        config["format"] = output_format
    kwargs["output_config"] = config
    resp = client.beta.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise RuntimeError("the model declined this request")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return text, getattr(resp, "_request_id", None)


def _log(s, purpose: str, version: str, status_: str, problems: List[str], request_id: Optional[str]) -> None:
    s.add(LlmCall(purpose=purpose, prompt_version=version, model=MODEL, status=status_, violations=problems,
                  request_id=request_id, created_at=datetime.now()))


def improve_draft(s, template_body: str, facts: str, audience: str) -> Dict:
    """Rewrite a template draft. Returns {used, body, reason}; on any problem the template is kept."""
    enabled, why = status()
    if not enabled:
        return {"used": False, "body": template_body, "reason": why}
    system = (
        "You polish short business messages written by an Account Manager at a payments company. "
        "Rewrite the draft so it reads naturally and stays concise. Keep every fact, number and date exactly "
        "as given and add none. Do not make commitments about pricing, discounts, SLAs or the roadmap. "
        + ("The reader is the customer: never mention internal scoring, priorities, internal call notes or "
           "suspicions, or competitors. " if audience == "customer" else "The reader is an internal colleague. ")
        + "Return only the message body, with the greeting and sign-off from the draft."
    )
    user = f"Facts you may use:\n{facts}\n\nDraft to rewrite:\n{template_body}"
    try:
        text, rid = _call(system, user)
    except Exception as exc:  # network, auth, refusal, old SDK: fall back, never break the page
        _log(s, "draft", DRAFT_PROMPT_VERSION, "error", [repr(exc)[:200]], None)
        return {"used": False, "body": template_body, "reason": f"Claude call failed, kept the template ({exc.__class__.__name__})."}
    problems = check_draft(text, facts + "\n" + template_body, audience)
    _log(s, "draft", DRAFT_PROMPT_VERSION, "fallback" if problems else "used", problems, rid)
    if problems:
        return {"used": False, "body": template_body, "reason": "Guardrails rejected the rewrite: " + "; ".join(problems)}
    return {"used": True, "body": text.strip(), "reason": "Rewritten by Claude and checked by the guardrails."}


EXTRACT_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {"risks": {"type": "array", "items": {"type": "string"}},
                       "opportunities": {"type": "array", "items": {"type": "string"}}},
        "required": ["risks", "opportunities"], "additionalProperties": False,
    },
}


def extract_from_note(s, note_text: str) -> Dict:
    """Re-extract risks and opportunities from a call summary. Returns {used, risks, opportunities, reason}."""
    enabled, why = status()
    if not enabled:
        return {"used": False, "reason": why}
    system = ("Extract account risks and opportunities from an Account Manager's call notes. Use only what the "
              "notes explicitly say. Each item is at most 8 words. At most 4 of each. If the customer showed "
              "interest in a specific product, phrase it as 'Interest in <product>'.")
    try:
        text, rid = _call(system, note_text, EXTRACT_SCHEMA)
        data = json.loads(text)
    except Exception as exc:
        _log(s, "extract", EXTRACT_PROMPT_VERSION, "error", [repr(exc)[:200]], None)
        return {"used": False, "reason": f"Claude call failed ({exc.__class__.__name__})."}
    problems = check_extraction(data, note_text)
    _log(s, "extract", EXTRACT_PROMPT_VERSION, "fallback" if problems else "used", problems, rid)
    if problems:
        return {"used": False, "reason": "Guardrails rejected the extraction: " + "; ".join(problems)}
    return {"used": True, "risks": data["risks"], "opportunities": data["opportunities"], "reason": "Extracted by Claude."}
