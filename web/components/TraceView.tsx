"use client";

import { useEffect, useState } from "react";
import { getTrace, type Span, type Trace } from "@/lib/api";

function pretty(value: unknown): string {
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value, null, 2);
}

function SpanRow({ span, index }: { span: Span; index: number }) {
  const tokens =
    span.type === "llm" ? ` · ${span.input_tokens?.toLocaleString()} in / ${span.output_tokens?.toLocaleString()} out` : "";
  return (
    <details className={`span span-${span.type}${span.is_error ? " span-error" : ""}`}>
      <summary>
        <span className="span-index">{index + 1}</span>
        <span className="span-type">{span.type}</span>
        <strong>{span.name}</strong>
        <span className="muted small">
          {span.latency_s.toFixed(2)} s{tokens}
          {span.cost_usd ? ` · $${span.cost_usd.toFixed(4)}` : ""}
        </span>
      </summary>
      <div className="span-body">
        <div>
          <div className="muted small">Input</div>
          <pre>{pretty(span.input)}</pre>
        </div>
        <div>
          <div className="muted small">Output</div>
          <pre>{pretty(span.output)}</pre>
        </div>
      </div>
    </details>
  );
}

export default function TraceView({ traceId, onClose }: { traceId: string; onClose: () => void }) {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getTrace(traceId).then(setTrace, (e) => setError(String(e)));
  }, [traceId]);

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <h2>Trace</h2>
          <button className="link" onClick={onClose}>
            Close
          </button>
        </div>
        {error && <div className="notice warn">{error}</div>}
        {trace && (
          <>
            <p className="muted small">
              {trace.mode} · {trace.model} · {trace.totals.latency_s.toFixed(1)} s ·{" "}
              {trace.totals.llm_calls} model calls · {trace.totals.tool_calls} tool calls ·{" "}
              {trace.totals.input_tokens.toLocaleString()} in / {trace.totals.output_tokens.toLocaleString()} out · $
              {trace.totals.cost_usd.toFixed(4)}
            </p>
            {trace.error && <div className="notice warn">{trace.error}</div>}
            {trace.spans.map((span, i) => (
              <SpanRow key={i} span={span} index={i} />
            ))}
          </>
        )}
      </aside>
    </div>
  );
}
