"use client";

import { useRef, useState } from "react";
import AnswerView from "@/components/AnswerView";
import TraceView from "@/components/TraceView";
import { ask, research, type Answer, type Step } from "@/lib/api";

type Mode = "ask" | "research";

// The six demo tasks; each is also a gold question (eval/gold/questions.jsonl).
const EXAMPLES: { mode: Mode; question: string }[] = [
  { mode: "ask", question: "What were Apple's total revenues in fiscal years 2023, 2024 and 2025?" },
  {
    mode: "ask",
    question: "What risks does Microsoft identify regarding its dependence on cloud infrastructure and data centers?",
  },
  { mode: "research", question: "Compare Apple's and Microsoft's R&D spending from 2023–2025." },
  {
    mode: "research",
    question: "Calculate the year-over-year percentage change in Apple's R&D spending from 2023 to 2025.",
  },
  {
    mode: "research",
    question:
      "Investigate how Apple's revenue mix changed over the last three years. Identify the major changes and summarize the factors management cited.",
  },
  { mode: "ask", question: "What will Apple's revenue be in 2027?" },
];

export default function Home() {
  const [mode, setMode] = useState<Mode>("ask");
  const [question, setQuestion] = useState("");
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState<Step[]>([]);
  const [thoughts, setThoughts] = useState<string[]>([]);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [traceId, setTraceId] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  async function submit(q = question, m = mode) {
    if (!q.trim() || running) return;
    setQuestion(q);
    setMode(m);
    setRunning(true);
    setSteps([]);
    setThoughts([]);
    setAnswer(null);
    setError(null);
    try {
      if (m === "ask") {
        setAnswer(await ask(q));
      } else {
        abort.current = new AbortController();
        await research(
          q,
          {
            onStep: (step) => setSteps((s) => [...s, step]),
            onThought: (text) => setThoughts((t) => [...t, text]),
            onAnswer: setAnswer,
            onError: setError,
          },
          abort.current.signal,
        );
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError(String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <main>
      <header>
        <h1>10k-agent</h1>
        <p className="muted">
          Grounded, cited answers from SEC 10-K filings · 8 companies · fiscal years 2023–2025
        </p>
      </header>

      <form
        className="ask-form"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <div className="toggle" role="radiogroup" aria-label="Mode">
          {(["ask", "research"] as Mode[]).map((m) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={mode === m}
              className={mode === m ? "active" : ""}
              onClick={() => setMode(m)}
            >
              {m === "ask" ? "Ask" : "Research"}
            </button>
          ))}
        </div>
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={
            mode === "ask"
              ? "A factual question, e.g. What was Netflix's revenue in 2024?"
              : "A comparison, calculation or 'why' question"
          }
          rows={3}
        />
        <div className="form-row">
          <span className="muted small">
            {mode === "ask" ? "Finds passages, answers with citations (~5 s)" : "Plans, searches, calculates and verifies (~60 s)"}
          </span>
          {running && mode === "research" ? (
            <button type="button" onClick={() => abort.current?.abort()}>
              Stop
            </button>
          ) : (
            <button type="submit" disabled={running || !question.trim()}>
              {running ? "Working…" : mode === "ask" ? "Ask" : "Research"}
            </button>
          )}
        </div>
      </form>

      {!answer && !running && steps.length === 0 && (
        <section className="examples">
          <div className="muted small">Try a demo task</div>
          {EXAMPLES.map((ex) => (
            <button key={ex.question} className="example" onClick={() => submit(ex.question, ex.mode)}>
              <span className="badge">{ex.mode === "ask" ? "Ask" : "Research"}</span> {ex.question}
            </button>
          ))}
        </section>
      )}

      {(steps.length > 0 || (running && mode === "research")) && (
        <section className="steps">
          <ol>
            {steps.map((step) => (
              <li key={step.n} className={step.is_error ? "step-error" : ""}>
                <div>{step.label}</div>
                <div className="muted small">
                  {step.summary} · {step.latency_s.toFixed(2)} s
                </div>
              </li>
            ))}
            {running && <li className="step-running">Thinking…</li>}
          </ol>
          {thoughts.length > 0 && (
            <details>
              <summary className="muted small">Agent notes</summary>
              {thoughts.map((t, i) => (
                <p key={i} className="small">
                  {t}
                </p>
              ))}
            </details>
          )}
        </section>
      )}

      {running && mode === "ask" && <p className="muted">Searching the filings…</p>}
      {error && <div className="notice warn">{error}</div>}
      {answer && <AnswerView answer={answer} onShowTrace={() => setTraceId(answer.trace_id)} />}
      {traceId && <TraceView traceId={traceId} onClose={() => setTraceId(null)} />}
    </main>
  );
}
