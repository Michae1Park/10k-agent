"""Render a gold question file as a readable PDF for human review.

HTML is built here and printed with headless Chrome, so there is no extra dependency.
"""

import re
import shutil
import subprocess
import tempfile
from collections import Counter
from html import escape
from pathlib import Path

from tenk_agent import gold
from tenk_agent.store import Store

CONTEXT_CHARS = 700
CHROME = ("google-chrome", "chromium", "chromium-browser")

STYLE = """
@page { size: A4; margin: 14mm 12mm; }
body { font: 10pt/1.4 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #1a1a1a; }
h1 { font-size: 20pt; margin: 0 0 4mm; }
h2 { font-size: 12pt; margin: 0; }
table { border-collapse: collapse; }
.summary td, .summary th { padding: 1mm 4mm 1mm 0; text-align: left; }
.q { border: 1px solid #bbb; border-radius: 2mm; padding: 3mm 4mm; margin: 0 0 5mm;
     break-inside: avoid-page; }
.q.long { break-inside: auto; }
.head { display: flex; justify-content: space-between; align-items: baseline; gap: 4mm; }
.id { font: 9pt ui-monospace, Menlo, Consolas, monospace; color: #444; }
.tags span { font-size: 8pt; border-radius: 1mm; padding: 0 1.5mm; margin-left: 1mm;
             background: #e8eaf0; }
.ok { background: #dff3e2 !important; color: #14532d; }
.bad { background: #fde0e0 !important; color: #8a1c1c; }
.warn { background: #fdf0d2 !important; color: #7a4b00; }
.question { font-size: 11.5pt; font-weight: 600; margin: 2mm 0; }
.label { font-size: 8pt; text-transform: uppercase; letter-spacing: .05em; color: #666;
         margin: 2.5mm 0 .5mm; }
.expected td, .expected th { padding: .5mm 4mm .5mm 0; text-align: left; font-weight: normal; }
.expected td.num { font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; }
ul { margin: 0; padding-left: 5mm; }
.source { border-left: 2px solid #8896b8; padding-left: 3mm; margin: 1.5mm 0; }
.source .meta { font-size: 9pt; }
pre { font: 8pt/1.35 ui-monospace, Menlo, Consolas, monospace; background: #f6f6f8;
      padding: 1.5mm 2mm; margin: 1mm 0; white-space: pre-wrap; overflow-wrap: anywhere; }
mark { background: #ffe38a; }
.notes { font-size: 9pt; color: #444; font-style: italic; }
a { color: #1d4ed8; }
"""


def render_pdf(gold_path: Path, store: Store, data_dir: Path, out: Path) -> int:
    """Write the full review PDF; returns the number of questions rendered."""
    questions = gold.load(gold_path)
    html = build_html(gold_path.name, questions, store, data_dir)
    _print_pdf(html, out)
    return len(questions)


def render_summary_pdf(gold_path: Path, out: Path) -> None:
    """Write the one-row-per-question overview: category, question and expected answer."""
    _print_pdf(build_summary_html(gold_path.name, gold.load(gold_path)), out)


SUMMARY_STYLE = """
@page { size: A4; margin: 10mm 9mm; }
body { font: 7.5pt/1.3 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #1a1a1a; }
h1 { font-size: 13pt; margin: 0 0 1mm; }
p { margin: 0 0 2mm; color: #555; }
table { border-collapse: collapse; width: 100%; }
th { text-align: left; background: #e8eaf0; padding: 1mm 1.5mm; }
td { padding: .8mm 1.5mm; vertical-align: top; border-top: 1px solid #ddd; }
tr { break-inside: avoid; }
thead { display: table-header-group; }
td.cat { white-space: nowrap; color: #333; }
td.num { color: #777; text-align: right; width: 6mm; }
td:nth-child(2) { width: 45%; }
td.abstain { font-style: italic; }
.mode { color: #8a5a00; font-weight: 600; }
tr.group td { background: #d5dae8; font-weight: 700; font-size: 8.5pt; border-top: 0;
            break-after: avoid; }
"""


CATEGORY_ORDER = (
    "retrieval", "grounding", "multi_document", "tool_use", "agent_workflow", "failure_case"
)


def build_summary_html(title: str, questions: list[dict]) -> str:
    ordered = sorted(
        questions,
        key=lambda q: (
            CATEGORY_ORDER.index(q["category"]) if q["category"] in CATEGORY_ORDER else 99,
            q.get("failure_mode") or "",
        ),
    )
    rows, current = [], None
    for i, q in enumerate(ordered, 1):
        if q["category"] != current:
            current = q["category"]
            count = sum(x["category"] == current for x in ordered)
            rows.append(f"<tr class=group><td colspan=3>{escape(current)} ({count})</td></tr>")
        rows.append(
            f"<tr><td class=num>{i}</td><td>{_mode(q)}{escape(q['question'])}</td>"
            f"<td>{_answer(q)}</td></tr>"
        )
    return (
        f"<!doctype html><meta charset=utf-8><title>{escape(title)} summary</title>"
        f"<style>{SUMMARY_STYLE}</style><h1>Gold set summary: {escape(title)}</h1>"
        f"<p>{len(questions)} questions, grouped by category</p>"
        "<table><thead><tr><th>#</th><th>Question</th>"
        f"<th>Expected answer</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _mode(q: dict) -> str:
    return f"<span class=mode>[{escape(q['failure_mode'])}]</span> " if q.get("failure_mode") else ""


def _answer(q: dict) -> str:
    if q.get("should_abstain"):
        return "<i>Abstain</i>"
    values = gold.expected_values(q)
    if values:
        many = len(values) > 1
        lines = []
        for v in values:
            who = " ".join(str(v[k]) for k in ("company", "fiscal_year") if k in v)
            lines.append(f"{f'{escape(who)}: ' if who and many else ''}<b>{v['value']:,}</b> {escape(v['unit'])}")
        return "<br>".join(lines)
    return "; ".join(escape(p) for p in q.get("required_points", []))


def build_html(title: str, questions: list[dict], store: Store, data_dir: Path) -> str:
    cards = [_card(q, store, data_dir) for q in questions]
    return (
        f"<!doctype html><meta charset=utf-8><title>{escape(title)}</title>"
        f"<style>{STYLE}</style><h1>Gold set: {escape(title)}</h1>"
        f"{_summary(questions)}{''.join(cards)}"
    )


def _summary(questions: list[dict]) -> str:
    def rows(counter: Counter) -> str:
        return "".join(f"<tr><td>{escape(str(k))}</td><td>{v}</td></tr>" for k, v in counter.items())

    verified = sum(map(gold.is_verified, questions))
    return (
        "<table class=summary><tr><th>Questions</th>"
        f"<td>{len(questions)} ({verified} verified)</td></tr></table>"
        "<table class=summary><tr><td valign=top><table class=summary>"
        f"{rows(Counter(q['category'] for q in questions))}</table></td>"
        "<td valign=top><table class=summary>"
        f"{rows(Counter(q['split'] for q in questions))}</table></td>"
        "<td valign=top><table class=summary>"
        f"{rows(Counter(q['answer_type'] for q in questions))}</table></td></tr></table>"
        "<p class=notes>Per question: yellow = quoted evidence found in the ingested filing; "
        "red tags mark evidence that could not be found or an expected value with no matching "
        "XBRL fact (the latter can be fine for non-standard line items).</p><hr>"
    )


def _card(q: dict, store: Store, data_dir: Path) -> str:
    resolved = gold.resolve_evidence(store, q)
    checks = gold.xbrl_checks(q, data_dir)
    sources = q.get("sources", []) + q.get("acceptable_sources", [])
    n_primary = len(q.get("sources", []))

    tags = [f"<span>{escape(q['split'])}</span>", f"<span>{escape(q['category'])}</span>"]
    if q.get("failure_mode"):
        tags.append(f"<span>{escape(q['failure_mode'])}</span>")
    for tag in q.get("tags", []):
        tags.append(f"<span>{escape(tag)}</span>")
    tags.append(f"<span>{escape(q['origin'])}</span>")
    if gold.is_verified(q):
        tags.append(f"<span class=ok>verified {escape(q['verified_by'])}</span>")
    else:
        tags.append("<span class=warn>unverified</span>")
    if any(not ids and s.get("evidence") for s, ids in zip(sources, resolved, strict=True)):
        tags.append("<span class=bad>evidence not found</span>")
    if checks:
        hit = sum(c.matched_concept is not None for c in checks)
        tags.append(f"<span class={'ok' if hit == len(checks) else 'bad'}>xbrl {hit}/{len(checks)}</span>")

    parts = [
        f"<div class=head><h2 class=id>{escape(q['id'])}</h2><div class=tags>{''.join(tags)}</div></div>",
        f"<div class=question>{escape(q['question'])}</div>",
        _expected(q, checks),
    ]
    for i, (source, chunk_ids) in enumerate(zip(sources, resolved, strict=True)):
        parts.append(_source(store, source, chunk_ids, acceptable=i >= n_primary))
    if q.get("expected_tools"):
        parts.append(f"<div class=label>Expected tools</div>{escape(', '.join(q['expected_tools']))}")
    if q.get("notes"):
        parts.append(f"<div class=label>Notes</div><div class=notes>{escape(q['notes'])}</div>")
    return f"<div class=q>{''.join(parts)}</div>"


def _expected(q: dict, checks: list) -> str:
    out = ["<div class=label>Expected answer</div>"]
    if q.get("should_abstain"):
        out.append("<b>Abstain</b> (the filings do not support an answer)")
    values = gold.expected_values(q)
    if values:
        by_value = {(c.value, c.unit): c.matched_concept for c in checks}
        rows = []
        for v in values:
            who = " ".join(str(v[k]) for k in ("company", "fiscal_year") if k in v)
            concept = by_value.get((v["value"], v["unit"]))
            xbrl = f"<td>{escape(concept)}</td>" if concept else (
                "<td class=notes>no matching XBRL fact</td>" if (v["value"], v["unit"]) in by_value else "<td></td>"
            )
            rows.append(
                f"<tr><th>{escape(who)}</th><td class=num>{v['value']:,}</td>"
                f"<td>{escape(v['unit'])}</td>{xbrl}</tr>"
            )
        tol = q.get("tolerance")
        out.append(f"<table class=expected>{''.join(rows)}</table>")
        if tol is not None:
            out.append(f"<span class=notes>tolerance {tol:.2%}</span>")
    if q.get("required_points"):
        items = "".join(f"<li>{escape(p)}</li>" for p in q["required_points"])
        out.append(f"<div class=label>Answer must say</div><ul>{items}</ul>")
    return "".join(out)


def _source(store: Store, source: dict, chunk_ids: list[str], acceptable: bool) -> str:
    kind = "Acceptable alternative source" if acceptable else "Source"
    url = store.filing_url(source["company"], source["fiscal_year"])
    link = f' · <a href="{escape(url)}">filing</a>' if url else ""
    found = "" if chunk_ids or not source.get("evidence") else " · <b class=bad>NOT FOUND</b>"
    meta = (
        f"<div class=meta><b>{kind}:</b> {escape(source['company'])} FY{source['fiscal_year']}, "
        f"Item {escape(str(source.get('item', '?')))} — {escape(source.get('section') or '')}"
        f"{link}{found}</div>"
    )
    evidence = source.get("evidence")
    if not evidence:
        return f"<div class=source>{meta}<div class=notes>No evidence quote (answer comes from XBRL).</div></div>"
    excerpt = _excerpt(store, source, chunk_ids, evidence)
    return f"<div class=source>{meta}{excerpt}</div>"


def _excerpt(store: Store, source: dict, chunk_ids: list[str], evidence: str) -> str:
    """The evidence in its surrounding filing text, highlighted; the bare quote if not found."""
    pattern = re.compile(
        r"[\s|$]+".join(re.escape(t) for t in re.split(r"[\s|$]+", evidence.strip()) if t),
        re.IGNORECASE,
    )
    for chunk in store.chunks(source["company"], source["fiscal_year"]):
        if chunk.id not in chunk_ids:
            continue
        m = pattern.search(chunk.text)
        if not m:
            continue
        start = chunk.text.rfind("\n", 0, max(0, m.start() - CONTEXT_CHARS)) + 1
        end = chunk.text.find("\n", min(len(chunk.text), m.end() + CONTEXT_CHARS))
        end = len(chunk.text) if end == -1 else end
        before, hit, after = chunk.text[start : m.start()], m.group(), chunk.text[m.end() : end]
        prefix = "…\n" if start else ""
        suffix = "\n…" if end < len(chunk.text) else ""
        units = f"<div class=notes>units: {escape(chunk.units)}</div>" if chunk.units else ""
        return (
            f"{units}<pre>{prefix}{escape(before)}<mark>{escape(hit)}</mark>"
            f"{escape(after)}{suffix}</pre>"
        )
    return f"<pre><mark>{escape(evidence)}</mark></pre>"


def _print_pdf(html: str, out: Path) -> None:
    chrome = next((p for name in CHROME if (p := shutil.which(name))), None)
    if chrome is None:
        raise RuntimeError("PDF rendering needs Chrome or Chromium on PATH")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "gold.html"
        page.write_text(html)
        subprocess.run(
            [
                chrome, "--headless", "--no-sandbox", "--disable-gpu",
                "--no-pdf-header-footer", f"--print-to-pdf={out.resolve()}", page.as_uri(),
            ],
            check=True,
            capture_output=True,
        )
