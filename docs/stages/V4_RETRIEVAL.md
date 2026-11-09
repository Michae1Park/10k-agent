# V4 · Retrieval

How `search_filings` finds passages. The search itself is V1's ([V1_RETRIEVAL.md](V1_RETRIEVAL.md)); V4 adds filtering by period, form and as-of date. Rules live in the [design doc §6](../V4_TECHNICAL_DESIGN_DOC.md#6-retrieval).

<p align="center">
  <img src="../img/v4-search.svg" alt="search_filings in V4: slots resolved from arguments or query, one sub-question per company and filing, keyword and dense search under the as-of filter, fusion, parents, rerank, round-robin merge" width="100%">
</p>

| Step | What it does | Why |
| --- | --- | --- |
| 1 Resolve | Companies and periods from the arguments or the query; no period given = every filing visible at the as-of | Searches the right filings |
| 2 Split | One sub-question per company and filing, naming only that company | Each company gets its own search |
| 3 Search | Keyword (BM25) and vector search, top 40 each, only filings before the as-of | Keywords catch figures and names; vectors catch paraphrase |
| 4 Fuse | Reciprocal rank fusion (k = 60) | Rewards passages both searches found |
| 5 Expand | Swap each small chunk for its parent | Small chunks match precisely; parents give full context |
| 6 Rerank | Cross-encoder scores each parent | Best passages first |
| 7 Merge | Round-robin across sub-questions | No company crowds out the others |

**Tests:** `test_retrieval.py` (aliases, fan-out, fusion, explicit companies and years override the query); `test_asof.py` (the date filter). V1's retrieval gold runs in the regression check.
