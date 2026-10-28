"""The answer the model returns, made safe to use (docs/stages/V2_GENERATION.md).

The model returns JSON (format: prompts/answer.md): an answer with [n] markers and a list of
claims, each with its value, unit and chunk-ID citations. `finalize` normalizes it, checks
each claim against the chunks it cites (verify.py) and lists the unverified claims.
"""

from tenk_agent.generation.verify import verify_claim
from tenk_agent.store import Store


def finalize(raw: dict, store: Store, sec=None, book=None) -> dict:
    """Normalize the model's JSON and verify each claim (sec: V3's XBRL facts, if cited)."""
    claims = []
    for i, claim in enumerate(raw.get("claims") or [], 1):
        if not isinstance(claim, dict):
            continue
        claim = {
            "id": claim.get("id", i),
            "text": str(claim.get("text", "")),
            "value": claim.get("value"),
            "unit": claim.get("unit"),
            "company": claim.get("company"),
            "fiscal_year": claim.get("fiscal_year"),
            "chunk_ids": _ids(claim.get("chunk_ids")),
            "calculation": claim.get("calculation"),
        } | ({"period": claim["period"]} if "period" in claim else {})  # V4 Ask
        claims.append(verify_claim(claim, store, sec, book))
    return {
        "answer": str(raw.get("answer", "")),
        "claims": claims,
        "table": raw.get("table") if isinstance(raw.get("table"), dict) else None,
        "scope_notice": raw.get("scope_notice"),
        "abstained": bool(raw.get("abstained", False)),
        "unverified": [c["id"] for c in claims if c["status"] == "unverified"],
        "format_error": bool(raw.get("format_error")),
    }


def _ids(value) -> list[str]:
    """Chunk IDs as a list, whether the model wrote a list or a single string."""
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(v) for v in value or []] if isinstance(value, list) else []


def unparsed(text: str) -> dict:
    """Fallback when the model never produced valid JSON: show the text, claim nothing."""
    return {
        "answer": text,
        "claims": [],
        "abstained": False,
        "scope_notice": None,
        "table": None,
        "format_error": True,
    }
