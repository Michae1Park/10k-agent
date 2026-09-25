# Gold-Set Authoring Guide

2026-09-26 · Related: [PRD.md](../PRD.md) §7–8 · [project-plan.md](../project-plan.md) (Evaluation)

**Every gold question has one unambiguous expected answer, verified by hand against the filing, with the exact source text recorded.** The evaluation is only as credible as this dataset, so these rules are strict on purpose.

## Where it lives

- `eval/gold/questions.jsonl`: one question per line.
- The file is append-mostly. See [Changing a question](#changing-a-question).

## Record format

```json
{
  "id": "aapl-rd-fy2024",
  "split": "dev",
  "category": "retrieval",
  "failure_mode": null,
  "question": "What was Apple's R&D expense in fiscal 2024?",
  "answer_type": "number",
  "expected_answer": {"value": "<verified value>", "unit": "USD millions"},
  "tolerance": 0.005,
  "required_points": [],
  "should_abstain": false,
  "sources": [
    {
      "company": "AAPL",
      "fiscal_year": 2024,
      "accession_no": "<accession number>",
      "item": "8",
      "section": "Consolidated Statements of Operations",
      "evidence": "<verbatim text or table row copied from the filing>"
    }
  ],
  "acceptable_sources": [],
  "expected_tools": ["search_filings"],
  "notes": "",
  "origin": "manual",
  "verified_by": "<initials>",
  "verified_on": "2026-10-01"
}
```

`gold_chunk_ids` is not written by hand. After ingestion, a script finds the chunks whose text contains each `evidence` string and fills them in. That's why evidence must be copied verbatim.

## Field rules

| Field | Rule |
| --- | --- |
| `id` | `<ticker>-<topic>-<fiscal year or range>`, lowercase, e.g. `msft-cloud-risk-fy2025`, `aapl-msft-rd-fy2023-2025`. Never reused. |
| `split` | `dev` (~70%) or `test` (~30%). Prompts and retrieval are tuned on `dev` only. `test` is run for reported results. |
| `category` | One of: `retrieval`, `grounding`, `multi_document`, `tool_use`, `agent_workflow`, `failure_case` (PRD §7). |
| `failure_mode` | Required when category is `failure_case`, otherwise `null`. Values below. |
| `question` | Plain English a non-expert would ask. See [Writing good questions](#writing-good-questions). |
| `answer_type` | `number`, `number_set` (several values, e.g. three years), `text`, or `abstain`. |
| `expected_answer` | Numbers: `{value, unit}`, or a list of them for `number_set`, each with its `fiscal_year`. Text: `null` (use `required_points`). Abstain: `null`. |
| `tolerance` | Relative tolerance for numbers. Default `0.005` (0.5%). Use `0.01` for calculated percentages to allow rounding. |
| `required_points` | For `text` answers: 2–5 short facts the answer must contain, each traceable to a source. |
| `should_abstain` | `true` only when the corpus can't answer the question. |
| `sources` | The primary source(s) that answer the question. At least one, unless the question is an abstention. |
| `acceptable_sources` | Other places in the corpus that also correctly state the answer (see [Which filing is the source](#which-filing-is-the-source)). Used so retrieval metrics don't penalize a correct alternative. |
| `expected_tools` | The minimum tool set a correct answer needs, e.g. `["search_filings", "calculator"]`. |
| `notes` | Anything a reviewer needs: restatements, judgment calls, why an answer is scoped a certain way. |
| `origin` | `manual` (written by hand) or `drafted` (generated as a draft, then reviewed). Lets results be compared by origin to check that drafted questions don't favor one model. |
| `verified_by` / `verified_on` | Empty / `null` until a person completes the verification procedure. Unverified questions are excluded from reported results. |

### Failure modes

Match PRD §6.6 exactly:

| `failure_mode` | Example |
| --- | --- |
| `ambiguous_fiscal_year` | "What was Apple's revenue in 2024?" |
| `shifted_fiscal_year` | "What was NVIDIA's revenue in 2024?" |
| `similar_numbers` | A metric shown for three years side by side |
| `unit_normalization` | Comparing figures reported in different units or periods |
| `entity_naming` | "Google" → Alphabet |
| `unsupported` | Forecasts, companies or years outside the corpus |
| `false_premise` | A question that assumes something the filings contradict |

Each failure mode needs at least two questions.

## Numbers and units

- **Record the value exactly as the filing's table reports it,** in the table's own unit. If the table says "(in millions)" and shows 31,370, record `31370` with `"unit": "USD millions"`. Don't convert to billions.
- **Units vocabulary:** `USD millions`, `USD thousands`, `USD`, `percent`, `shares millions`, `count`. Add a new unit only if none fits, and list it here.
- **Percentages** are stored as percent numbers: 12.5% becomes `12.5` with `"unit": "percent"`.
- **Calculated answers** (growth rates, shares of a total): compute from the verified inputs with a script or spreadsheet, never by hand. Record the inputs in `notes`.
- **Negative values:** store them as negative numbers, even if the filing shows parentheses.

## Fiscal years

- **Always use the filer's own fiscal-year label**, as printed on the 10-K cover page. NVIDIA's fiscal year ending January 2025 is `fiscal_year: 2025`.
- A question that uses a bare calendar year ("in 2024") for a company whose fiscal year doesn't match the calendar is a `failure_case`. Its `notes` must say how it should be interpreted.
- The corpus covers **FY2023–FY2025** only (PRD §6.1). A question about any other year is an `unsupported` failure case, even if an older value appears as a comparative column.

## Which filing is the source

A value often appears in several 10-Ks. For example, FY2024 R&D appears in both the FY2024 and FY2025 filings.

1. **Primary source is the filing for that fiscal year.** FY2024 R&D comes from the FY2024 10-K.
2. **The same value in a later filing** goes in `acceptable_sources`.
3. **If a later filing restates the value** (it differs), the primary source still wins. Record the restated value and filing in `notes`, and do not list the later filing as acceptable.
4. **For narrative questions** ("What risks does…"), the source is the requested year's filing. If no year is given, use the latest (FY2025) and say so in `notes`.

## Text answers

- Write `required_points` as short, checkable facts, not a model answer: "Names data center capacity constraints as a risk", not a paragraph.
- Every point must be traceable to a `sources` entry, with its `evidence` quote.
- Leave out anything an answer could reasonably omit. The LLM judge scores against these points, so vague points make vague scores.

## Abstention questions

- `answer_type: "abstain"`, `expected_answer: null`, `should_abstain: true`, `sources: []`.
- `notes` states what a good answer does, e.g. "Declines to forecast; states the corpus covers FY2023–FY2025."
- Include near-misses, not just obvious ones: a real metric for a year outside the corpus, or a company that isn't in the corpus.

## Verification procedure

Do this for every question before it's committed:

1. Open the filing on EDGAR (not a secondary site) and find the answer.
2. Copy the `evidence` text verbatim: the exact sentence(s), or for a table row, the row label and its cells in order, separated by ` | ` (e.g. `Total net sales | 391,035 | 383,285 | 394,328`). Record the column years and unit in `section` or `notes` if they aren't obvious.
3. Record `accession_no`, `item` and `section`.
4. For numeric answers on standard line items, cross-check against the SEC's XBRL company facts data. If they disagree, the filing text wins; note the discrepancy.
5. Set `verified_by` and `verified_on`.
6. A second pass is optional but recommended: re-verify a random ~20% of questions a few days later.

## Writing good questions

- **Write from the filing, not from system output.** Never write a question by looking at what the retriever returned; that bakes the retriever's blind spots into the test.
- **Answerable by a non-expert reading the filing.** No questions that need outside financial knowledge.
- **One unambiguous answer.** If two readers could reasonably disagree, tighten the question or make it a failure case on purpose.
- **Vary the phrasing.** Mix "R&D", "research and development spending" and "how much did X invest in research". Mix explicit years and "last year".
- **Cover every company and every year.** Each company should appear in at least 6 questions, and no single company in more than 25%.
- **Don't put the answer in the question.** For example, don't write "Apple's Services revenue grew; by how much?" when growth itself is what's being tested.

## Target mix

About 100 questions (project plan, Evaluation): 30 retrieval · 15 grounding · 15 multi-document · 15 tool use · 10 agent workflow · 15 failure cases. The first 30, needed for V0, should cover every category at least twice.

## Changing a question

- **Never silently edit** an expected answer, source or tolerance. Results across versions must stay comparable.
- If a gold answer turns out to be wrong, fix it in its own commit (`fix: correct gold answer for <id>`) and re-run every version's reported results.
- To retire a question, add `"retired": true` with a reason in `notes`. Don't delete it and don't reuse its `id`.
- **Never change the gold set to make a failing run pass.**

## Checklist per question

- [ ] Plain-language question with exactly one correct answer
- [ ] Category, and failure mode if it's a failure case, set
- [ ] Value in the table's own unit; unit from the vocabulary
- [ ] Filer's fiscal-year label used
- [ ] Primary source is that year's filing; alternatives in `acceptable_sources`
- [ ] Evidence copied verbatim from EDGAR
- [ ] Numeric value cross-checked against XBRL (standard line items)
- [ ] Split assigned; verified by / on set
