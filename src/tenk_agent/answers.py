"""The answer format shared by Ask and Research modes (docs/STAGE3_ANSWER.md).

The model returns JSON: an answer with [n] markers and a list of claims, each with its
value, unit and chunk-ID citations. `finalize` then runs the deterministic verification
pass (V4), attaches citation metadata for the UI, and lists unverified claims.
"""

from tenk_agent.store import Store
from tenk_agent.verify import verify_claim

CORPUS_SCOPE = "10-K filings for 8 companies (Apple, Microsoft, Amazon, Alphabet, Meta, NVIDIA, Tesla, Netflix), fiscal years 2023–2025"

RULES = """Rules:
- Use only the filings. Every number you state must come from a chunk you were given or a tool result, never from memory.
- Fiscal years are the filer's own labels from the corpus table below. A bare calendar year ("in 2024") for a company whose fiscal year doesn't end in December is ambiguous: pick the fiscal year that ended in (or mostly covers) that calendar year, and say which fiscal year and period end you used. NVIDIA's fiscal 2025 ended in January 2025, so it mostly covers calendar 2024.
- Take each value from the column for the requested year; tables show several years side by side.
- Before comparing companies, put values in the same unit and say which fiscal periods are being compared.
- Company names: "Google" is Alphabet (GOOGL); "Facebook" is Meta (META).
- If the filings can't answer (a forecast, a company or year outside the corpus, a figure the filings don't report), say so plainly, set "abstained": true, and state what the corpus does cover. Never guess.
- If the question contains a false premise, correct it using the filings.
- No investment advice, stock prices or forecasts."""

ANSWER_FORMAT = """Reply with only a JSON object, no other text:
{
  "answer": "The answer in plain language. Cite claims with markers like [1], [2] matching claim ids.",
  "claims": [
    {
      "id": 1,
      "text": "The factual statement, e.g. Apple's R&D expense was $31,370 million in fiscal 2024.",
      "value": 31370,
      "unit": "USD millions",
      "company": "AAPL",
      "fiscal_year": 2024,
      "chunk_ids": ["AAPL-FY2024-8-012"],
      "calculation": null
    }
  ],
  "table": null,
  "scope_notice": null,
  "abstained": false
}
- One claim per number or fact. "value" is the number exactly as the source shows it, in the source table's unit ("USD millions", "USD thousands", "USD", "percent", "count", "shares millions"); use null for statements without a number.
- "chunk_ids" are the IDs of the chunks that contain the claim. Cite only chunk IDs you were actually shown.
- For a calculated number, set "calculation" to {"op": ..., "expression": ..., "inputs": {...}, "input_unit": "USD millions", "result": ...} exactly as the calculator returned it, and cite the chunks holding the inputs.
- "table" (optional) is {"columns": [...], "rows": [[...], ...]} for comparisons across years or companies.
- "scope_notice" is a short note on what the corpus covers when part of the question falls outside it."""


def corpus_table(store: Store) -> str:
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
