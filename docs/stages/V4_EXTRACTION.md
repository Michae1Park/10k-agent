# V4 · Extraction (layer 1): how it works

Companion to [design doc §7](../V4_TECHNICAL_DESIGN_DOC.md#7-extraction-layer-1), which holds the contract. This doc does not restate the rules; where the two disagree, the design doc wins.

## The extraction

<p align="center">
  <img src="../img/v4-extraction.svg" alt="KPI and guidance extraction: one LLM call per release, code validation of quote and value, one repair turn, exception rows, reconciliation with XBRL" width="100%">
</p>

```text
per release:
  chunks  = the release's chunks visible at its filing date, tables first, up to 40k characters
            (slide-image releases: the chunks naming the most registry labels)
  reply   = one LLM call (extract.md, registry in the prompt)
  for each row: chunk id given? quote verbatim? value in quote (unit-aware)? metric/segment in registry?
  failures → one repair turn showing the chunk text → still failing → exception row
  store valid rows with definition_version, method, filed
```

## Implementation notes

| Note | Why |
|---|---|
| Thinking off, temperature 0 | With thinking on, every call spent its tokens reasoning and returned no JSON (build log §3.7) |
| No elided quotes ("…"); a series is quoted from label to number | Elided quotes failed the verbatim check (build log §3.7) |
| A prior-year column is rejected (`growth_pair` finds the later column) | Two-column tables invite the prior-year value (build log §3.7) |
| Tables first, then registry-matched chunks, up to `MAX_CHARS` | Fits the 12k-token budget with the registry; slide releases have no tables (build log §3.6, §3.10) |
| `forecast.anchor()` converts a growth or margin guide to a level via the year-ago actual | Design §7.3 keeps the basis; the forecast needs a level |

## Tests

`test_extract.py`: valid rows kept and bad ones repaired once; validation rejects an unknown metric, segment or a value not in its quote; only that release's visible chunks are sent; `growth_pair` finds the later column; `definition_version` follows a recast; the registry covers all eight; a quote miss shows the chunk text. Gold: 337 labels, `evaluation/gold/extraction-gold.jsonl`; result 98.5% exact, 100% critical ([V4_RESULTS.md](../V4_RESULTS.md)).
