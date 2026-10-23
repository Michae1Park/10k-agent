"""A finished V2 run as a Markdown report: one table per kind of answer, with each question's
expected answer, retrieval scores (Recall@k, reciprocal rank of the first gold chunk) and
verdict. The retrieval scores are computed here from the chunks the model was shown, so the
report is end to end: retrieval, then the answer.
"""

from tenk_agent.evaluation import gold as gold_set
from tenk_agent.evaluation.retrieval_metrics import gold_chunks, recall_at, reciprocal_rank
from tenk_agent.evaluation.scoring import summarize
from tenk_agent.store import Store

RECALL_AT = (1, 5, 8)
POINT_CHARS = 110
VERDICTS = {True: "correct", False: "wrong", None: "unjudged"}


def build_report(
    label: str,
    rows: list[dict],
    questions: list[dict],
    store: Store,
    context: str = "retrieved",
    at: tuple[int, ...] = RECALL_AT,
) -> str:
    """`rows`: a run's results.json; `context`: which of its contexts to report (the end-to-end
    one is `retrieved`: V1's own top k)."""
    by_id = {q["id"]: q for q in questions}
    rows = [r for r in rows if r["context"] == context and r["id"] in by_id]
    groups = {
        "number": [r for r in rows if by_id[r["id"]]["answer_type"] in ("number", "number_set")],
        "text": [r for r in rows if by_id[r["id"]]["answer_type"] == "text"],
        "abstain": [r for r in rows if by_id[r["id"]].get("should_abstain")],
    }
    scores = summarize(rows)
    parts = [
        f"# End-to-end report: {label}",
        f"{len(rows)} questions, context `{context}`. "
        f"Correct: {_pct(scores['correct'])} of {scores['scored']} scored"
        + (f"; unjudged: {', '.join(scores['unjudged'])}" if scores["unjudged"] else "")
        + ".",
        "## Number questions",
        "Correct when every expected value is stated in the answer within the question's "
        "tolerance. Recall@k and MRR score the retrieval step on the same question.",
        _number_table(groups["number"], by_id, store, at),
        "## Text questions",
        "Correct when the LLM judge finds every required point in the answer.",
        _text_table(groups["text"], by_id, store),
        "## Questions to decline",
        "Correct when the model declines. There is no gold chunk, so no Recall or MRR.",
        _decline_table(groups["abstain"], by_id),
    ]
    return "\n\n".join(parts) + "\n"


def _number_table(rows: list[dict], by_id: dict, store: Store, at: tuple[int, ...]) -> str:
    header = ["Question", "Expected", "Source", *(f"R@{k}" for k in at), "MRR", "Numbers", "Result"]
    body, recalls, rrs = [], [], []
    for row in rows:
        q = by_id[row["id"]]
        gold = gold_chunks(store, q)
        recall = [recall_at(row["chunks"], gold, k) for k in at]
        rr = reciprocal_rank(row["chunks"], gold)
        recalls.append(recall)
        rrs.append(rr)
        numbers = row["numbers"]
        body.append(
            [
                _cell(f"{q['question']} ({q['id']})"),
                _expected(q),
                _sources(q, store),
                *(f"{r:.2f}" for r in recall),
                f"{rr:.2f}",
                f"{numbers['matched']}/{numbers['expected']}",
                VERDICTS[row["correct"]],
            ]
        )
    if rows:
        mean = [sum(col) / len(rows) for col in zip(*recalls, strict=True)]
        correct = sum(r["correct"] is True for r in rows) / len(rows)
        body.append(
            ["**Mean**", "", "", *(f"**{m:.2f}**" for m in mean), f"**{sum(rrs) / len(rrs):.2f}**"]
            + ["", f"**{correct:.0%}**"]
        )
    return _table(header, body)


def _text_table(rows: list[dict], by_id: dict, store: Store) -> str:
    body = []
    for row in rows:
        q = by_id[row["id"]]
        covered = row["points"] or [None] * len(q["required_points"])
        marks = {True: "yes", False: "no", None: "?"}
        points = "<br>".join(
            f"{marks[c]}: {_cell(_short(p))}"
            for p, c in zip(q["required_points"], covered, strict=True)
        )
        body.append(
            [
                _cell(f"{q['question']} ({q['id']})"),
                points,
                _sources(q, store),
                f"{sum(bool(c) for c in covered)}/{len(covered)}" if row["points"] else "–",
                VERDICTS[row["correct"]],
            ]
        )
    if rows:
        correct = sum(r["correct"] is True for r in rows) / len(rows)
        body.append(["**Correct**", "", "", "", f"**{correct:.0%}**"])
    header = ["Question", "Required points (yes = covered)", "Source", "Covered", "Result"]
    return _table(header, body)


def _decline_table(rows: list[dict], by_id: dict) -> str:
    body = [
        [
            _cell(f"{by_id[r['id']]['question']} ({r['id']})"),
            "declined" if r["abstained"] else "answered",
            VERDICTS[r["correct"]],
        ]
        for r in rows
    ]
    if rows:
        body.append(
            ["**Correct**", "", f"**{sum(r['correct'] is True for r in rows) / len(rows):.0%}**"]
        )
    return _table(["Question", "Model", "Result"], body)


def _sources(q: dict, store: Store) -> str:
    """Where in the filings the answer is, to cross-check it: company and year (linked to the
    filing), Item and the printed page of the chunk holding each source's evidence quote."""
    sources = q.get("sources", [])
    resolved = gold_set.resolve_evidence(store, q)[: len(sources)]
    lines = []
    for source, chunk_ids in zip(sources, resolved, strict=True):
        name = f"{source['company']} FY{source['fiscal_year']}"
        url = store.filing_url(source["company"], source["fiscal_year"])
        parts = [f"[{name}]({url})" if url else name, f"Item {source.get('item', '?')}"]
        chunk = store.get_chunk(chunk_ids[0]) if chunk_ids else None
        if page := _pages(chunk):
            parts.append(page)
        lines.append(_cell(" · ".join(parts)))
    return "<br>".join(lines)


def _pages(chunk) -> str | None:
    """'p. 48' or 'pp. 48–49': the numbers printed on the pages, else the document's page."""
    if chunk is None:
        return None
    pages, prefix = (chunk.printed_pages, "") if chunk.printed_pages else (chunk.pages, "PDF ")
    if not pages:
        return None
    first, last = pages[0], pages[-1]
    return f"{prefix}p. {first}" if first == last else f"{prefix}pp. {first}–{last}"


def _expected(q: dict) -> str:
    lines = []
    for v in gold_set.expected_values(q):
        who = " ".join(str(v[k]) for k in ("company", "fiscal_year") if k in v)
        lines.append(f"{who + ': ' if who else ''}{v['value']:,} {v['unit']}")
    return "<br>".join(lines)


def _table(header: list[str], body: list[list[str]]) -> str:
    if not body:
        return "None."
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    return "\n".join(lines + ["| " + " | ".join(row) + " |" for row in body])


def _cell(text: str) -> str:
    return " ".join(text.replace("|", "\\|").split())


def _short(text: str) -> str:
    return text if len(text) <= POINT_CHARS else text[: POINT_CHARS - 1].rstrip() + "…"


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value:.1%}"
