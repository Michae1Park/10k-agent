# 10k-agent — Product Requirements

2026-09-25 · Status: Draft · Implementation details: [project-plan.md](project-plan.md)

## 1. Summary

**10k-agent is an agentic research assistant that uses RAG, tool calling and automated evaluation to answer grounded questions across SEC filings.**

It retrieves, compares and analyzes information from companies' annual reports (10-Ks). Every answer is backed by citations to the source filing. When the filings don't support an answer, it says so.

The core question the product answers:

> Can an agent reliably find information across long, messy documents, use tools to analyze it, and give grounded answers with citations — and can we prove it works?

## 2. Problem

A general-purpose LLM can often answer "What was Apple's R&D spending in 2024?" from memory. It can't reliably do this:

> "Compare Apple's R&D spending with Microsoft's for 2022–2024, calculate year-over-year growth for each, verify every number against the original 10-K, and give me a cited table."

That request needs the system to:

- find the right filings for each company
- identify the right fiscal years
- retrieve the right tables and extract the numbers
- normalize units
- do the arithmetic
- verify each value against its source
- cite it
- flag anything it couldn't verify

The model could attempt all of this in one prompt, but nobody could tell whether the result is right.

10-K filings make this hard on purpose. They run 100–300 pages. They repeat the same metric across sections and years. They are semi-structured, cross-reference themselves, and use fiscal calendars that differ from company to company.

## 3. What 10k-agent is, and isn't

| 10k-agent is | 10k-agent is not |
| --- | --- |
| A research assistant over SEC filings | An AI financial analyst |
| "Perform reliable research over SEC filings" | "Ask me anything about finance" |
| Grounded, cited answers from primary sources | Stock picks or investment recommendations |
| Explicit about the limits of its evidence | A forecaster |
| A small, deliberate toolset | A pile of 15 tools |

The filings are the environment. The product is document intelligence: correct retrieval, correct calculation, correct citation, and honest uncertainty.

## 4. Users and use

- **Primary user:** someone researching public companies who wants answers they can check, without being a finance expert.
- **Secondary audience:** engineers and hiring managers reviewing the project. For them, the evaluation results and traces show that the system works.

Questions should make sense to a non-expert. Examples: "How much did X spend on R&D?", "What risks does X describe?", "Why did X's revenue mix change?"

## 5. How it works (conceptual)

The LLM does the reasoning. Tools give it access to things it shouldn't invent: the filings, exact numbers and arithmetic.

```
                         10k-agent
                             │
             ┌───────────────┴───────────────┐
             │                               │
        Simple questions              Complex questions
             │                               │
             ▼                               ▼
      Ask mode (RAG)               Research mode (Agent)
                                             │
                              ┌──────────────┼──────────────┐
                              ▼              ▼              ▼
                          Retrieval     Calculator     Verification
```

In Research mode the agent has three jobs:

1. **Decide what information is needed.** For example, "Why did Apple's gross margin change?" needs both the financial statements and management's discussion of the change, not just the top five matching passages.
2. **Decide which tools to use, and in what order.** The agent chooses the workflow.
3. **Check its own work.** It confirms that each cited source actually contains the claimed number. If the evidence is missing, the answer says "I couldn't verify this claim."

## 6. Product requirements

### 6.1 Corpus

- **R1.** Annual 10-K filings for 8 companies: Apple, Microsoft, Amazon, Alphabet (Google), Meta, NVIDIA, Tesla and Netflix.
- **R2.** The 3 most recent fiscal years per company, 24 filings in total (~2,400–7,200 pages).
- **R3.** Every filing is tagged with company, fiscal year, period end date and section, so answers can cite them precisely.

### 6.2 Modes

| | Ask | Research |
| --- | --- | --- |
| For | Simple, factual questions | Comparisons, calculations, "why" questions |
| Example | "What was Apple's total revenue in 2024?" | "Research Apple's iPhone revenue growth over the past three years and explain the factors management identified." |
| Behavior | Retrieve → rerank → answer with citations | Multi-step plan: find filings, locate data, calculate, search management discussion, cross-check, report |
| User sees | Answer + cited passages | Live progress of each step, then a structured report with citations |

- **R4.** The user can choose between Ask and Research.
- **R5.** Research mode shows its steps as it works, so the difference between RAG and agent behavior is visible.

### 6.3 Question classes

The product must handle three classes of question:

| Class | Examples | What the system must do |
| --- | --- | --- |
| **Retrieval** | "What does Microsoft say about risks associated with cloud computing?" · "What does Tesla identify as its primary competition?" · "What percentage of Apple's revenue came from services in 2024?" | Find the relevant passages in long filings and answer with citations |
| **Comparative** | "How did Apple's R&D spending change between 2023 and 2025?" · "Compare Microsoft's and Google's revenue growth over the last three reported fiscal years." | Retrieve across several filings and keep company, fiscal year, metric, units and reporting period straight |
| **Agentic** | "Compare Apple's and Microsoft's R&D spending over the last three years and calculate the percentage change." | Plan multiple steps, use tools, extract data, calculate, then answer with citations |

### 6.4 Tools

The agent's toolset is deliberately small. The interesting problem is whether the agent can decide when and how to use each tool.

| Tool | Purpose | Release |
| --- | --- | --- |
| `search_filings` | Find relevant passages across the corpus | MVP |
| `get_filing` | Read a specific filing or section in full | MVP |
| `calculator` | Do all arithmetic; the LLM never calculates in its head | MVP |
| `search_web` | Look outside the filings, clearly labeled as a non-filing source | Later |
| `generate_chart` | Visualize extracted figures | Later |

### 6.5 Answers

- **R6.** Every factual claim has a citation to the company, fiscal year and filing section.
- **R7.** Every number is either taken from a cited source or calculated by the calculator tool from cited inputs.
- **R8.** Claims that can't be verified are labeled as unverified, not presented as fact.
- **R9.** Questions the filings can't answer get an explicit statement of what the corpus does and doesn't cover.

### 6.6 Required behaviors on known failure cases

| Failure case | Example | Required behavior |
| --- | --- | --- |
| Ambiguous fiscal year | "What was Apple's revenue in 2024?" | Resolve it to the fiscal year that ended September 2024, and say so |
| Similar numbers | A table lists three years of the same metric side by side | Take the value for the requested year and section, not a neighboring one |
| Cross-company units | "Compare Apple's and Microsoft's R&D spending." | Normalize units and periods before comparing |
| Unsupported question | "What will Apple's revenue be in 2027?" | Decline to predict: "The filings in this corpus don't provide a verified 2027 revenue figure. I found historical revenue through [year]…" |

## 7. Demo tasks

These six tasks are representative workflows that prove the system works. They don't limit what it can answer. Each one maps to an evaluation category.

| # | Task | Demonstrates | Evaluated by |
| --- | --- | --- | --- |
| 1 | "What were Apple's total revenues in fiscal years 2022, 2023 and 2024?" | Ingestion, chunking, retrieval, reranking, cited answer (the RAG baseline) | Retrieval: recall / relevance |
| 2 | "What risks does Microsoft identify regarding its dependence on cloud infrastructure and data centers?" | Semantic retrieval across several sections, synthesis | Grounding: faithfulness |
| 3 | "Compare Apple's and Microsoft's R&D spending from 2022–2024." | Cross-document retrieval, fiscal-year alignment | Multi-document: completeness |
| 4 | "Calculate the year-over-year percentage change in Apple's R&D spending from 2022 to 2024." | Extraction, then calculator tool | Tool use: tool correctness |
| 5 | "Investigate how Apple's revenue mix changed over the last three years. Identify the major changes and summarize the factors management cited." | Full research workflow: find filings, extract tables, calculate, read MD&A, cross-check, report with citations | Agent workflow: task success |
| 6 | "What will Apple's revenue be in 2027?" | Grounding and uncertainty handling | Abstention: unsupported-claim rate |

Task 5 is the flagship demo.

## 8. Success metrics

Success means being able to say: **"Evaluated on 100 manually verified questions across 8 companies and 3 fiscal years."** Most of the metrics below are scored against that gold set.

**Gold dataset:** 50–100 questions, each with a verified expected answer, source filing and source section.

| Layer | Metrics |
| --- | --- |
| Retrieval | Recall@5, MRR, context relevance |
| Answer | Correctness, faithfulness, citation accuracy |
| Agent | Task completion, tool selection, tool-call correctness, number of unnecessary calls |
| Guardrails | Unsupported-answer rate on questions the corpus can't answer |

The same evaluation suite runs after every release, so each architectural addition has to show a measured improvement. Results are reported as measured, never estimated.

## 9. Releases

Each version adds one capability and is evaluated against the previous one.

| Version | Capability | Flow |
| --- | --- | --- |
| **V1 — Search** | Find relevant passages | Question → retriever → relevant SEC passages |
| **V2 — RAG** | Cited answers | Question → retriever → LLM → cited answer |
| **V3 — Agent** | Multi-step tool use | Question → agent (search, retrieve, calculator) → answer |
| **V4 — Reliable agent** | Verified, observable answers | Question → agent → tools → verification → citations → evaluation / tracing |

The deliverable is a results table with one column per version (V1–V4). It shows why each layer exists.

## 10. Scope

| Component | MVP | Later | Out of scope |
| --- | --- | --- | --- |
| Companies | 8 | More | |
| Filing type | 10-K | 10-Q | |
| Years | 3 | | |
| RAG + reranking | Yes | | |
| Hybrid search | Optional | | |
| Agent | Yes | | |
| Tools | 3–4 | Web search, charts | |
| Calculations | Yes | | |
| Citations | Yes | | |
| Evaluation dataset | 50–100 questions | | |
| Observability | Yes | | |
| Stock prices | | | Excluded |
| Investment recommendations | | | Excluded |

Stock prices and investment advice stay out of scope permanently. They don't help show the core technical problem, and they would pull the project into a much larger financial-domain problem.

## 11. Architecture (reference only)

The architecture stays simple. It is a Next.js frontend, a FastAPI backend, the agent with a RAG tool, a filing tool and a calculator, and a vector database over the filings. An evaluation system writes traces and metrics. The infrastructure is not the project; the system's behavior is. See [project-plan.md](project-plan.md) for the technical design.
