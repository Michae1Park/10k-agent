#!/usr/bin/env python3
"""Check each method's chunks against the hand-checkable sanity cases (spec §4.7).

Each case quotes a few words of the filing and says what the chunk holding them must carry: Item,
heading path, kind, units, text it must or must not share a chunk with, or that it's in no chunk.
Matching is on letters and digits only, like score.py. Parent-child is checked on its parents.

  python experiments/v0/sanity.py                     # cases and run label from the defaults
  python experiments/v0/sanity.py --cases my.yaml --label other-run

Reads output/chunking/<label>/chunks-<method>.json; writes sanity.json there.
"""

import argparse
import json
from pathlib import Path

import yaml
from score import key, scale

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).resolve().parent / "config.yaml"
CASES = ROOT / "experiments" / "V0_testdata" / "aapl-fy25-sanity.yaml"
METHODS = ["rule", "parent_child", "agentic"]
RANK = {"ok": 0, "coarser": 1, "fail": 2}


def finds(case: dict) -> list[str]:
    return case["find"] if isinstance(case["find"], list) else [case["find"]]


def check(case: dict, chunks: list[dict]) -> tuple[str, list[str]]:
    """("ok" | "coarser" | "fail", reasons). Coarser: the path is an ancestor of the expected."""
    keyed = [(c, key(c["text"])) for c in chunks]
    holders, missing = {}, []  # chunk id → (chunk, its key)
    for f in finds(case):
        hits = {c["id"]: (c, k) for c, k in keyed if key(f) in k}
        if not hits:
            missing.append(f"not in any chunk (split or dropped): {f[:50]!r}")
        holders |= hits
    if case.get("absent"):
        return ("fail", [f"in {', '.join(holders)}"]) if holders else ("ok", [])
    if missing:
        return "fail", missing

    errors, coarser = [], []
    want = [key(p) for p in case["path"]]
    for cid, (c, k) in holders.items():
        if c["item"] != case["item"]:
            errors.append(f"{cid}: item {c['item']}, not {case['item']}")
        if "kind" in case and c["kind"] != case["kind"]:
            errors.append(f"{cid}: kind {c['kind']}, not {case['kind']}")
        if "units" in case and scale(c.get("units")) != scale(case["units"]):
            errors.append(f"{cid}: units {c.get('units')!r}, not {case['units']!r}")
        errors += [f"{cid}: missing {w[:50]!r}" for w in case.get("with", []) if key(w) not in k]
        errors += [f"{cid}: also holds {w[:50]!r}" for w in case.get("without", []) if key(w) in k]
        mine = [key(p) for p in c["heading_path"]]
        if mine != want:
            path = f"{cid}: path {' › '.join(c['heading_path']) or '[]'}"
            (coarser if want[: len(mine)] == mine else errors).append(path)
    return ("fail" if errors else "coarser" if coarser else "ok"), errors + coarser


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--cases", type=Path, default=CASES, help="default: %(default)s")
    ap.add_argument("--label", help="run label (default: run.label in config.yaml)")
    args = ap.parse_args()
    label = args.label or yaml.safe_load(CONFIG.read_text())["run"]["label"]
    out = ROOT / "output" / "chunking" / label
    spec = yaml.safe_load(args.cases.read_text(encoding="utf-8"))
    cases = spec["cases"]
    methods = [m for m in METHODS if (out / f"chunks-{m}.json").exists()]

    results = {}
    for m in methods:
        chunks = json.loads((out / f"chunks-{m}.json").read_text())
        graded = {c["id"]: check(c, chunks) for c in cases}
        results[m] = {cid: {"status": s, "reasons": r} for cid, (s, r) in graded.items()}

    print(f"{'case':<5} {'tests':<62} " + " ".join(f"{m:<13}" for m in methods))
    for c in cases:
        cells = " ".join(f"{results[m][c['id']]['status']:<13}" for m in methods)
        print(f"{c['id']:<5} {c['tests'][:62]:<62} {cells}")
    for m in methods:
        counts = {s: sum(r["status"] == s for r in results[m].values()) for s in RANK}
        print(f"\n{m}: " + ", ".join(f"{n} {s}" for s, n in counts.items()))
        for cid, r in results[m].items():
            for reason in r["reasons"]:
                print(f"  {cid} {r['status']}: {reason}")

    checked = sum(bool(c.get("checked")) for c in cases)
    if spec["review"]["status"] != "reviewed":
        print(f"\nCases are a draft: {checked}/{len(cases)} checked by a person.")
    (out / "sanity.json").write_text(
        json.dumps({"cases": str(args.cases), "review": spec["review"], **results}, indent=1)
    )


if __name__ == "__main__":
    main()
