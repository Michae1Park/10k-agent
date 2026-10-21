"""The answer the model returns, made safe to use (docs/V2_GENERATION.md).

The model returns JSON (format: prompts/answer.md): an answer with [n] markers and a list of
claims, each with its value, unit and chunk-ID citations. `finalize` normalizes it, checks
each claim against the chunks it cites (verify.py), attaches citation metadata and lists the
unverified claims.
"""

from tenk_agent.generation.verify import verify_claim
from tenk_agent.store import Store


def corpus_table(store: Store) -> str:
    """The prompt's {corpus}: every filing's fiscal-year label and period end, per company."""
    lines = ["Corpus (filer's fiscal-year label: period end date):"]
    by_company: dict[str, list[str]] = {}
    names = {}
    for f in store.filings():
        names[f.ticker] = f.company_name
        by_company.setdefault(f.ticker, []).append(f"FY{f.fiscal_year}: {f.period_end_date}")
    for ticker, years in by_company.items():
        lines.append(f"- {ticker} {names[ticker]} — " + "; ".join(years))
    return "\n".join(lines)


def finalize(raw: dict, store: Store, verify: bool = True) -> dict:
    """Normalize the model's JSON, verify each claim and attach citation metadata."""
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
        }
        if verify:
            claim = verify_claim(claim, store)
        else:
            claim |= {"status": "unchecked", "status_reason": ""}
        claims.append(claim)

    citations = {}
    for claim in claims:
        for chunk_id in claim["chunk_ids"]:
            if chunk_id in citations or not (chunk := store.get_chunk(chunk_id)):
                continue
            filing = store.filing(chunk.ticker, chunk.fiscal_year)
            citations[chunk_id] = {
                "chunk_id": chunk_id,
                "company": chunk.ticker,
                "company_name": filing.company_name if filing else chunk.ticker,
                "fiscal_year": chunk.fiscal_year,
                "period_end_date": filing.period_end_date if filing else None,
                "item": chunk.item,
                "section_title": store.section_titles(chunk.ticker, chunk.fiscal_year).get(
                    chunk.item
                ),
                "url": filing.source_url if filing else None,
                "pages": chunk.pages,
                "printed_pages": chunk.printed_pages,
                "kind": chunk.kind,
                "text": chunk.text,
            }

    return {
        "answer": str(raw.get("answer", "")),
        "claims": claims,
        "citations": citations,
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
