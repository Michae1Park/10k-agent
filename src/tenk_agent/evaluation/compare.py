"""Compare runs against a baseline: intervals, paired tests, error analysis, charts.

    tenk eval compare BASE=v4-research-qwen3.5-9b V7=v7-research-qwen3.5-9b [NAME=label ...]

The first run is the baseline. Writes eval/reports/<name>.md (tables, for the repo) and
<name>.html (self-contained, charts + the same tables). Only questions present in both
runs enter a paired test; every number comes from the runs' results.jsonl.
"""

import html
import json
from dataclasses import dataclass
from pathlib import Path

from tenk_agent.evaluation import analysis, stats

REPORTS_DIR = Path("eval/reports")
PRIMARY = "task_completion"


@dataclass
class Run:
    name: str
    label: str
    system: str
    model: str | None
    rows: list[dict]


def load(columns: list[tuple[str, str]]) -> list[Run]:
    runs = []
    for name, label in columns:
        config = analysis.load_config(label)
        runs.append(
            Run(name, label, config["system"], config.get("model"), analysis.load_rows(label))
        )
    return runs


def build(columns: list[tuple[str, str]]) -> dict:
    """Everything the report shows, as data (also written next to the report as JSON)."""
    runs = load(columns)
    base, candidates = runs[0], runs[1:]
    system = base.system
    metrics = stats.metrics_for(system)
    data = {
        "runs": [
            {"name": r.name, "label": r.label, "model": r.model, "questions": len(r.rows)}
            for r in runs
        ],
        "metrics": [],
        "paired": [],
        "mcnemar": {},
        "causes": {r.name: dict(analysis.causes(r.rows)) for r in runs},
        "categories": {},
        "transitions": {},
    }
    per_run = {r.name: stats.intervals(r.rows, system, metrics) for r in runs}
    per_candidate = {c.name: stats.paired(base.rows, c.rows, system, metrics) for c in candidates}
    for m in metrics:
        info = {"key": m.key, "fmt": m.fmt, "higher_is_better": m.higher_is_better}
        data["metrics"].append(
            info | {"label": m.label, "runs": {n: vars(i[m.key]) for n, i in per_run.items()}}
        )
        for name, results in per_candidate.items():
            data["paired"].append(
                info | {"metric": m.label, "candidate": name} | vars(results[m.key])
            )
    completion = [m for m in metrics if m.key == PRIMARY]
    for r in runs:
        data["categories"][r.name] = {
            name: vars(stats.intervals(group, system, completion)[PRIMARY])
            for name, group in analysis.slices(r.rows, "category").items()
        }
    for c in candidates:
        data["mcnemar"][c.name] = stats.mcnemar(base.rows, c.rows)
        data["transitions"][c.name] = analysis.transitions(base.rows, c.rows)
    rate = data["metrics"][0]["runs"][base.name]["value"] or 0.5
    data["questions_for_5pt"] = stats.questions_for_margin(min(max(rate, 0.05), 0.95))
    data["unverified"] = any(not row.get("verified") for r in runs for row in r.rows)
    return data


def write(columns: list[tuple[str, str]], out: Path | None = None) -> tuple[Path, Path]:
    data = build(columns)
    out = out or REPORTS_DIR / ("compare-" + "-vs-".join(n.lower() for n, _ in columns))
    out.parent.mkdir(parents=True, exist_ok=True)
    md, page = out.with_suffix(".md"), out.with_suffix(".html")
    md.write_text(markdown(data))
    page.write_text(render_html(data))
    out.with_suffix(".json").write_text(json.dumps(data, indent=2) + "\n")
    return md, page


# ---------------------------------------------------------------- formatting


def fmt(value, kind: str, signed: bool = False) -> str:
    if value is None:
        return "n/m"
    if kind == "pct":
        return f"{value * 100:+.1f} pt" if signed else f"{value * 100:.1f}%"
    return f"{value:+.2f}" if signed else f"{value:.2f}"


def span(low, high, kind: str, signed: bool = False) -> str:
    if low is None:
        return ""
    return f"[{fmt(low, kind, signed)}, {fmt(high, kind, signed)}]"


ALPHA = 0.05


def verdict(p: dict) -> str:
    """Plain-language reading of a paired difference, decided by the permutation test.

    Not by the bootstrap interval: with a handful of changed questions the percentile
    interval can exclude zero while the exact test can't reach significance (4 changes,
    all one way, give p = 0.125 at best).
    """
    if p["diff"] is None or p["p"] is None:
        return "not measured"
    if p["p"] >= ALPHA:
        return "no clear difference"
    better = (p["diff"] > 0) == p["higher_is_better"]
    return "better" if better else "worse"


# ---------------------------------------------------------------- markdown


def markdown(data: dict) -> str:
    names = [r["name"] for r in data["runs"]]
    base = names[0]
    lines = [f"# Comparison: {' vs '.join(names)}", ""]
    lines += [
        f"- **{r['name']}**: `{r['label']}` ({r['model']}, {r['questions']} questions)"
        for r in data["runs"]
    ]
    lines += [
        "",
        f"Baseline: {base}. Intervals are 95% bootstrap over questions; p-values are paired "
        f"sign-flip permutation tests on the questions both runs answered, and "
        f"a difference reads as better or worse only at p < {ALPHA}. "
        f"A ±5-point interval on task completion needs ~{data['questions_for_5pt']} questions.",
    ]
    if data["unverified"]:
        lines.append("\n**Provisional:** includes unverified gold questions.")

    lines += [
        "",
        "## Metrics",
        "",
        "| Metric | " + " | ".join(names) + " |",
        "| --- |" + " --- |" * len(names),
    ]
    for m in data["metrics"]:
        cells = [
            f"{fmt(i['value'], m['fmt'])} {span(i['low'], i['high'], m['fmt'])}".strip()
            for i in m["runs"].values()
        ]
        lines.append(f"| {m['label']} | " + " | ".join(cells) + " |")

    lines += [
        "",
        f"## Differences vs {base}",
        "",
        "| Metric | Run | Difference [95% CI] | p | Reading |",
        "| --- | --- | --- | --- | --- |",
    ]
    for p in data["paired"]:
        lines.append(
            f"| {p['metric']} | {p['candidate']} | {fmt(p['diff'], p['fmt'], True)} "
            f"{span(p['low'], p['high'], p['fmt'], True)} | {_p(p['p'])} | {verdict(p)} |"
        )

    lines += [
        "",
        "## Question-level changes (task completion)",
        "",
        "| Run | Fixed | Regressed | Both pass | Both fail | McNemar p |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, m in data["mcnemar"].items():
        lines.append(
            f"| {name} | {m['fixed']} | {m['regressed']} | {m['both_pass']} | {m['both_fail']} | {_p(m['p'])} |"
        )
    for name, rows in data["transitions"].items():
        changed = [t for t in rows if t["change"] in ("fixed", "regressed")]
        if changed:
            lines += [
                "",
                f"**{name}**: "
                + ", ".join(
                    f"`{t['id']}` {t['change']}"
                    + (f" ({t['candidate_cause']})" if t["change"] == "regressed" else "")
                    for t in changed
                ),
            ]

    lines += [
        "",
        "## Failure causes",
        "",
        "| Cause | " + " | ".join(names) + " |",
        "| --- |" + " --- |" * len(names),
    ]
    for cause in analysis.CAUSES:
        counts = [data["causes"][n].get(cause, 0) for n in names]
        if any(counts):
            lines.append(f"| {cause} | " + " | ".join(map(str, counts)) + " |")

    cats = sorted({c for per in data["categories"].values() for c in per})
    lines += [
        "",
        "## Task completion by category",
        "",
        "| Category | " + " | ".join(names) + " |",
        "| --- |" + " --- |" * len(names),
    ]
    for cat in cats:
        cells = []
        for n in names:
            i = data["categories"][n].get(cat)
            cells.append(f"{fmt(i['value'], 'pct')} (n={i['n']})" if i else "—")
        lines.append(f"| {cat} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _p(p) -> str:
    return "n/m" if p is None else ("<0.001" if p < 0.001 else f"{p:.3f}")


# ---------------------------------------------------------------- html

# Reference palette (dataviz skill), fixed slot order; validated light and dark.
SERIES_LIGHT = (
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#4a3aa7",
    "#e34948",
)
SERIES_DARK = (
    "#3987e5",
    "#d95926",
    "#199e70",
    "#c98500",
    "#d55181",
    "#008300",
    "#9085e9",
    "#e66767",
)


def _tokens(dark: bool) -> str:
    series = SERIES_DARK if dark else SERIES_LIGHT
    base = (
        "--page:#0d0d0d;--surface:#1a1a19;--ink:#ffffff;--ink-2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);--good:#0ca30c;--bad:#e66767;color-scheme:dark;"
        if dark
        else "--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink-2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--ring:rgba(11,11,11,.10);--good:#006300;--bad:#d03b3b;color-scheme:light;"
    )
    return base + "".join(f"--s{i + 1}:{c};" for i, c in enumerate(series))


CSS = """
:root{%LIGHT%}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){%DARK%}}
:root[data-theme="dark"]{%DARK%}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:40px 0 4px}
.sub,.note{color:var(--ink-2)}.note{font-size:13px;margin:6px 0 12px}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:16px;margin-top:12px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:16px}
.tile{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:14px 16px}
.tile .label{color:var(--ink-2);font-size:13px}.tile .value{font-size:28px;font-weight:600;margin:2px 0}
.tile .meta{color:var(--muted);font-size:13px}
.flag{display:inline-block;padding:2px 8px;border-radius:999px;border:1px solid var(--ring);font-size:12px;color:var(--ink-2)}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:var(--ink-2);margin:4px 0 8px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
svg{display:block;width:100%;min-width:560px;height:auto;overflow:visible}
code{overflow-wrap:anywhere}
svg text{fill:var(--muted);font-size:12px;font-variant-numeric:tabular-nums}
svg .ink{fill:var(--ink-2)}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--grid);white-space:nowrap}
th{color:var(--ink-2);font-weight:600}
.better{color:var(--good)}.worse{color:var(--bad)}
details{margin-top:8px}summary{cursor:pointer;color:var(--ink-2);font-size:13px}
#tip{position:fixed;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--ring);border-radius:8px;padding:6px 10px;font-size:12px;box-shadow:0 4px 16px rgba(0,0,0,.15);display:none;max-width:280px;z-index:9}
[data-tip]{cursor:default}
"""

TIP_JS = """
const tip=document.getElementById('tip');
document.addEventListener('mousemove',e=>{const t=e.target.closest('[data-tip]');
if(!t){tip.style.display='none';return}tip.textContent=t.dataset.tip;tip.style.display='block';
const x=Math.min(e.clientX+14,innerWidth-tip.offsetWidth-8);tip.style.left=x+'px';tip.style.top=(e.clientY+14)+'px'});
"""


def render_html(data: dict) -> str:
    names = [r["name"] for r in data["runs"]]
    color = {n: f"var(--s{i + 1})" for i, n in enumerate(names)}
    base = names[0]
    e = html.escape
    parts = [
        f"<h1>{e(' vs '.join(names))}</h1>",
        f"<p class='sub'>Baseline <b>{e(base)}</b> · "
        + " · ".join(
            f"{e(r['name'])}: <code>{e(r['label'])}</code> ({r['questions']} q)"
            for r in data["runs"]
        )
        + "</p>",
    ]
    if data["unverified"]:
        parts.append(
            "<p><span class='flag'>Provisional · includes unverified gold questions</span></p>"
        )
    parts.append(_tiles(data, names))
    parts.append(
        _section(
            "Differences vs " + base,
            "Each dot is candidate minus baseline on the same questions; the line is its 95% bootstrap interval. Hover for the permutation-test p-value, which decides better or worse (p < 0.05).",
            _forest(data, names, color),
        )
    )
    parts.append(
        _section(
            "Quality vs latency",
            "Task completion (with 95% interval) against mean seconds per question. Up and to the left is better.",
            _scatter(data, names, color),
        )
    )
    parts.append(
        _section(
            "Why questions fail",
            "Each failure is assigned to the first pipeline stage that broke. “citation” counts right answers citing a chunk that doesn't show the number.",
            _causes(data, names, color),
        )
    )
    parts.append(
        _section(
            "Task completion by category",
            "With 95% intervals; small categories have wide intervals.",
            _categories(data, names, color),
        )
    )
    parts.append(
        _section(
            "Question-level changes",
            "Task completion per question, baseline → candidate. Regressions first.",
            _transitions(data),
        )
    )
    parts.append(
        _section("All metrics", "Values with 95% bootstrap intervals.", _metric_table(data, names))
    )
    body = "\n".join(parts)
    css = CSS.replace("%LIGHT%", _tokens(False)).replace("%DARK%", _tokens(True))
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Eval comparison</title><style>{css}</style></head>"
        f"<body><main>{body}</main><div id='tip'></div><script>{TIP_JS}</script></body></html>"
    )


def _section(title: str, note: str, inner: str) -> str:
    return f"<h2>{html.escape(title)}</h2><p class='note'>{html.escape(note)}</p><div class='card'>{inner}</div>"


def _legend(names, color) -> str:
    return (
        "<div class='legend'>"
        + "".join(
            f"<span><i style='background:{color[n]}'></i>{html.escape(n)}</span>" for n in names
        )
        + "</div>"
    )


def _tiles(data, names) -> str:
    tiles = []
    for p in data["paired"]:
        if p["key"] != PRIMARY:
            continue
        m = data["mcnemar"][p["candidate"]]
        reading = verdict(p)
        cls = {"better": "better", "worse": "worse"}.get(reading, "")
        tiles.append(
            "<div class='tile'>"
            f"<div class='label'>{html.escape(p['candidate'])} vs {html.escape(names[0])} · task completion</div>"
            f"<div class='value'>{fmt(p['diff'], 'pct', True)}</div>"
            f"<div class='meta'>95% CI {span(p['low'], p['high'], 'pct', True)} · p {_p(p['p'])}</div>"
            f"<div class='meta'><span class='{cls}'>{reading}</span> · {m['fixed']} fixed, {m['regressed']} regressed</div>"
            "</div>"
        )
    tiles.append(
        "<div class='tile'><div class='label'>Questions for a ±5 pt interval</div>"
        f"<div class='value'>{data['questions_for_5pt']}</div>"
        f"<div class='meta'>vs {data['runs'][0]['questions']} in the baseline run</div></div>"
    )
    return "<div class='tiles'>" + "".join(tiles) + "</div>"


def _forest(data, names, color) -> str:
    rows = [p for p in data["paired"] if p["fmt"] == "pct" and p["diff"] is not None]
    if not rows:
        return "<p class='note'>Nothing to compare.</p>"
    labels = list(dict.fromkeys(p["metric"] for p in rows))
    cands = names[1:]
    extent = max(abs(v) for p in rows for v in (p["low"], p["high"], p["diff"]) if v is not None)
    limit = max(0.1, -(-extent * 10 // 1) / 10)  # round up to 10 points
    w, left, right, band = 720, 190, 20, 14 + 14 * len(cands)
    h = 24 + band * len(labels)
    x = lambda v: left + (v + limit) / (2 * limit) * (w - left - right)  # noqa: E731
    out = [
        f"<svg viewBox='0 0 {w} {h + 22}' role='img' aria-label='Metric differences with confidence intervals'>"
    ]
    for t in (-limit, -limit / 2, 0, limit / 2, limit):
        out.append(
            f"<line x1='{x(t):.1f}' x2='{x(t):.1f}' y1='12' y2='{h}' stroke='var({'--axis' if t == 0 else '--grid'})' stroke-width='1'/>"
        )
        out.append(f"<text x='{x(t):.1f}' y='{h + 16}' text-anchor='middle'>{t * 100:+.0f}</text>")
    for i, label in enumerate(labels):
        y0 = 18 + i * band
        hib = next(p for p in rows if p["metric"] == label)["higher_is_better"]
        out.append(
            f"<text class='ink' x='{left - 12}' y='{y0 + band / 2 + 4:.1f}' text-anchor='end'>{html.escape(label)}{'' if hib else ' ↓'}</text>"
        )
        for j, c in enumerate(cands):
            p = next((p for p in rows if p["metric"] == label and p["candidate"] == c), None)
            if not p:
                continue
            y = y0 + 10 + j * 14
            tip = f"{c} − {names[0]}: {fmt(p['diff'], 'pct', True)} {span(p['low'], p['high'], 'pct', True)}, p {_p(p['p'])}"
            out.append(f"<g data-tip='{html.escape(tip)}'>")
            out.append(
                f"<rect x='{left}' y='{y - 7}' width='{w - left - right}' height='14' fill='transparent'/>"
            )
            if p["low"] is not None:
                out.append(
                    f"<line x1='{x(p['low']):.1f}' x2='{x(p['high']):.1f}' y1='{y}' y2='{y}' stroke='{color[c]}' stroke-width='2' stroke-linecap='round'/>"
                )
            out.append(
                f"<circle cx='{x(p['diff']):.1f}' cy='{y}' r='4.5' fill='{color[c]}' stroke='var(--surface)' stroke-width='2'/></g>"
            )
    out.append("</svg>")
    note = "<p class='note'>Percentage points. ↓ = lower is better.</p>"
    return (
        (_legend(cands, color) if len(cands) > 1 else "")
        + "<div class='scroll'>"
        + "".join(out)
        + "</div>"
        + note
    )


def _scatter(data, names, color) -> str:
    comp = next((m for m in data["metrics"] if m["key"] == PRIMARY), None)
    lat = next((m for m in data["metrics"] if m["key"] == "mean_latency_s"), None)
    if not comp or not lat:
        return ""
    points = [
        (n, comp["runs"][n], lat["runs"][n]["value"])
        for n in names
        if lat["runs"][n]["value"] is not None and comp["runs"][n]["value"] is not None
    ]
    if not points:
        return ""
    w, h, left, bottom, top, right = 720, 290, 56, 36, 26, 90
    xmax = max(p[2] for p in points) * 1.15
    lows = [p[1]["low"] for p in points if p[1]["low"] is not None] + [
        p[1]["value"] for p in points
    ]
    ymin = max(0.0, (min(lows) * 10 // 1) / 10 - 0.1)
    xstep = _nice_step(max(p[2] for p in points) / 4)
    xmax = xstep * (-(-max(p[2] for p in points) * 1.1 // xstep))
    x = lambda v: left + v / xmax * (w - left - right)  # noqa: E731
    y = lambda v: top + (1 - (v - ymin) / (1 - ymin)) * (h - top - bottom)  # noqa: E731
    out = [
        f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='Task completion against latency per run'>"
    ]
    for k in range(round((1 - ymin) * 10) + 1):
        v = ymin + k / 10
        out.append(
            f"<line x1='{left}' x2='{w - right}' y1='{y(v):.1f}' y2='{y(v):.1f}' stroke='var(--grid)'/><text x='{left - 8}' y='{y(v) + 4:.1f}' text-anchor='end'>{v * 100:.0f}%</text>"
        )
    for k in range(round(xmax / xstep) + 1):
        v = xstep * k
        out.append(
            f"<text x='{x(v):.1f}' y='{h - bottom + 18}' text-anchor='middle'>{v:.0f}s</text>"
        )
    out.append(
        f"<line x1='{left}' x2='{w - right}' y1='{h - bottom}' y2='{h - bottom}' stroke='var(--axis)'/>"
    )
    for n, iv, latency in points:
        cx, cy = x(latency), y(iv["value"])
        tip = f"{n}: {fmt(iv['value'], 'pct')} {span(iv['low'], iv['high'], 'pct')}, {latency:.0f} s/question"
        out.append(
            f"<g data-tip='{html.escape(tip)}'><circle cx='{cx:.1f}' cy='{cy:.1f}' r='14' fill='transparent'/>"
        )
        if iv["low"] is not None:
            out.append(
                f"<line x1='{cx:.1f}' x2='{cx:.1f}' y1='{y(iv['low']):.1f}' y2='{y(iv['high']):.1f}' stroke='{color[n]}' stroke-width='2' stroke-linecap='round'/>"
            )
        out.append(
            f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='5' fill='{color[n]}' stroke='var(--surface)' stroke-width='2'/>"
        )
        out.append(
            f"<text class='ink' x='{cx + 12:.1f}' y='{cy - 8:.1f}'>{html.escape(n)}</text></g>"
        )
    out.append("</svg>")
    return _legend(names, color) + "<div class='scroll'>" + "".join(out) + "</div>"


def _causes(data, names, color) -> str:
    """One row per cause, one bar per run: runs keep their colors, causes are labels."""
    causes = [c for c in analysis.CAUSES if any(data["causes"][n].get(c) for n in names)]
    if not causes:
        return "<p class='note'>No failures.</p>"
    top = max(data["causes"][n].get(c, 0) for n in names for c in causes)
    w, left, right, bar = 720, 130, 40, 12
    band = bar * len(names) + 14
    h = band * len(causes) + 6
    x = lambda v: left + v / top * (w - left - right)  # noqa: E731
    out = [f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='Failure causes per run'>"]
    out.append(f"<line x1='{left}' x2='{left}' y1='2' y2='{h - 4}' stroke='var(--axis)'/>")
    for i, cause in enumerate(causes):
        y0 = 4 + i * band
        out.append(
            f"<text class='ink' x='{left - 12}' y='{y0 + band / 2 + 2:.1f}' text-anchor='end'>{html.escape(cause)}</text>"
        )
        for j, n in enumerate(names):
            count = data["causes"][n].get(cause, 0)
            y = y0 + j * bar
            tip = f"{n} · {cause}: {count} question{'s' if count != 1 else ''}"
            out.append(
                f"<g data-tip='{html.escape(tip)}'><rect x='{left}' y='{y}' width='{w - left - right}' height='{bar}' fill='transparent'/>"
            )
            if count:
                out.append(
                    f"<path d='{_bar(left, y + 1, x(count) - left, bar - 2)}' fill='{color[n]}'/>"
                )
            out.append(f"<text x='{x(count) + 6:.1f}' y='{y + bar - 2}'>{count}</text></g>")
    out.append("</svg>")
    return _legend(names, color) + "<div class='scroll'>" + "".join(out) + "</div>"


def _categories(data, names, color) -> str:
    cats = sorted(
        {c for per in data["categories"].values() for c, i in per.items() if i["value"] is not None}
    )
    if not cats:
        return ""
    w, left, right, bar = 720, 130, 60, 12
    band = bar * len(names) + 14
    h = band * len(cats) + 28
    x = lambda v: left + v * (w - left - right)  # noqa: E731
    out = [f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='Task completion by category'>"]
    for t in (0, 0.25, 0.5, 0.75, 1):
        out.append(
            f"<line x1='{x(t):.1f}' x2='{x(t):.1f}' y1='4' y2='{h - 22}' stroke='var({'--axis' if t == 0 else '--grid'})'/><text x='{x(t):.1f}' y='{h - 6}' text-anchor='middle'>{t * 100:.0f}%</text>"
        )
    for i, cat in enumerate(cats):
        y0 = 6 + i * band
        out.append(
            f"<text class='ink' x='{left - 12}' y='{y0 + band / 2 + 2:.1f}' text-anchor='end'>{html.escape(cat)}</text>"
        )
        for j, n in enumerate(names):
            iv = data["categories"][n].get(cat)
            if not iv or iv["value"] is None:
                continue
            y = y0 + j * bar
            tip = f"{n} · {cat}: {fmt(iv['value'], 'pct')} {span(iv['low'], iv['high'], 'pct')} (n={iv['n']})"
            out.append(
                f"<g data-tip='{html.escape(tip)}'><rect x='{left}' y='{y}' width='{w - left - right}' height='{bar}' fill='transparent'/>"
            )
            out.append(
                f"<path d='{_bar(x(0), y + 1, x(iv['value']) - x(0), bar - 2)}' fill='{color[n]}'/>"
            )
            if iv["low"] is not None:
                out.append(
                    f"<line x1='{x(iv['low']):.1f}' x2='{x(iv['high']):.1f}' y1='{y + bar / 2:.1f}' y2='{y + bar / 2:.1f}' stroke='var(--ink-2)' stroke-width='1'/>"
                )
            out.append("</g>")
    out.append("</svg>")
    return _legend(names, color) + "<div class='scroll'>" + "".join(out) + "</div>"


def _nice_step(raw: float) -> float:
    """1, 2 or 5 times a power of ten, at least `raw`."""
    import math

    power = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    return next(m * power for m in (1, 2, 5, 10) if m * power >= raw)


def _bar(x0: float, y: float, width: float, height: float, r: float = 4) -> str:
    """A horizontal bar, square at the baseline and rounded at the data end."""
    if width <= 0:
        return f"M{x0},{y}h1v{height}h-1z"
    r = min(r, width, height / 2)
    return (
        f"M{x0:.1f},{y:.1f}h{width - r:.1f}a{r},{r} 0 0 1 {r},{r}v{height - 2 * r:.1f}"
        f"a{r},{r} 0 0 1 {-r},{r}h{-(width - r):.1f}z"
    )


def _transitions(data) -> str:
    parts = []
    for name, rows in data["transitions"].items():
        m = data["mcnemar"][name]
        parts.append(
            f"<p><b>{html.escape(name)}</b>: {m['fixed']} fixed · {m['regressed']} regressed · "
            f"{m['both_pass']} both pass · {m['both_fail']} both fail · McNemar p {_p(m['p'])}</p>"
        )
        shown = [t for t in rows if t["change"] not in ("both pass", "unscored")]
        unscored = sum(t["change"] == "unscored" for t in rows)
        if unscored:
            parts.append(
                f"<p class='note'>{unscored} text questions not scored (they need the judge).</p>"
            )
        if not shown:
            continue
        body = "".join(
            f"<tr><td><code>{html.escape(t['id'])}</code></td><td>{html.escape(t['category'])}</td>"
            f"<td class='{ {'fixed': 'better', 'regressed': 'worse'}.get(t['change'], '') }'>{t['change']}</td>"
            f"<td>{t['base_cause'] or '—'}</td><td>{t['candidate_cause'] or '—'}</td></tr>"
            for t in shown
        )
        parts.append(
            "<div class='scroll'><table><tr><th>Question</th><th>Category</th><th>Change</th>"
            f"<th>Baseline cause</th><th>{html.escape(name)} cause</th></tr>{body}</table></div>"
        )
    return "".join(parts)


def _metric_table(data, names) -> str:
    head = "<tr><th>Metric</th>" + "".join(f"<th>{html.escape(n)}</th>" for n in names) + "</tr>"
    rows = ""
    for m in data["metrics"]:
        cells = "".join(
            f"<td>{fmt(i['value'], m['fmt'])} <span class='note'>{span(i['low'], i['high'], m['fmt'])}</span></td>"
            for i in m["runs"].values()
        )
        rows += f"<tr><td>{html.escape(m['label'])}</td>{cells}</tr>"
    return f"<div class='scroll'><table>{head}{rows}</table></div>"
