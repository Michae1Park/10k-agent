"use client";

import { useState } from "react";
import type { Answer, Citation, Claim } from "@/lib/api";

const STATUS_LABEL: Record<string, string> = {
  verified: "Verified",
  calculated: "Calculated",
  cited: "Cited",
  unverified: "Unverified",
  unchecked: "Unchecked",
};

function formatValue(claim: Claim): string | null {
  if (claim.value === null || claim.value === undefined) return null;
  const n = Number(claim.value);
  const shown = Number.isFinite(n) ? n.toLocaleString("en-US") : String(claim.value);
  return claim.unit ? `${shown} ${claim.unit}` : shown;
}

// Render text with [n] markers as buttons and **bold** spans; blank lines split paragraphs.
function RichText({ text, onCite }: { text: string; onCite: (id: number) => void }) {
  return (
    <>
      {text.split(/\n{2,}/).map((paragraph, p) => (
        <p key={p}>
          {paragraph.split(/(\[\d+(?:,\s*\d+)*\]|\*\*[^*]+\*\*|\n)/).map((part, i) => {
            const marker = part.match(/^\[(\d+(?:,\s*\d+)*)\]$/);
            if (marker) {
              return marker[1].split(/,\s*/).map((id) => (
                <button key={`${i}-${id}`} className="marker" onClick={() => onCite(Number(id))}>
                  {id}
                </button>
              ));
            }
            if (part.startsWith("**") && part.endsWith("**")) {
              return <strong key={i}>{part.slice(2, -2)}</strong>;
            }
            if (part === "\n") return <br key={i} />;
            return <span key={i}>{part}</span>;
          })}
        </p>
      ))}
    </>
  );
}

function CitationCard({ citation }: { citation: Citation }) {
  return (
    <div className="citation">
      <div className="citation-head">
        <strong>
          {citation.company_name} · FY{citation.fiscal_year}
        </strong>
        <span className="muted">
          10-K Item {citation.item}
          {citation.section_title ? ` — ${citation.section_title}` : ""}
          {citation.period_end_date ? ` · period ended ${citation.period_end_date}` : ""}
        </span>
        {citation.url && (
          <a href={citation.url} target="_blank" rel="noreferrer">
            Open filing on EDGAR ↗
          </a>
        )}
      </div>
      <pre className="chunk-text">{citation.text}</pre>
      <div className="muted small">{citation.chunk_id}</div>
    </div>
  );
}

export default function AnswerView({
  answer,
  onShowTrace,
}: {
  answer: Answer;
  onShowTrace: () => void;
}) {
  const [selected, setSelected] = useState<number | null>(null);
  const claim = answer.claims.find((c) => c.id === selected) ?? null;
  const unverified = answer.claims.filter((c) => c.status === "unverified");

  return (
    <section className="answer">
      {answer.abstained && <div className="notice">The filings in this corpus can't fully answer this.</div>}
      {answer.format_error && (
        <div className="notice warn">The model didn't return a structured answer; nothing below is verified.</div>
      )}
      <div className="answer-text">
        <RichText text={answer.answer} onCite={setSelected} />
      </div>
      {answer.scope_notice && <div className="notice">{answer.scope_notice}</div>}

      {answer.table && answer.table.columns?.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {answer.table.columns.map((c, i) => (
                  <th key={i}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {answer.table.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td key={c}>
                      <RichText text={String(cell ?? "")} onCite={setSelected} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {claim && (
        <div className="citation-panel">
          <div className="citation-panel-head">
            <span>
              [{claim.id}] {claim.text}
            </span>
            <button className="link" onClick={() => setSelected(null)}>
              Close
            </button>
          </div>
          <div className={`status status-${claim.status}`}>
            {STATUS_LABEL[claim.status]}
            {claim.status_reason ? ` — ${claim.status_reason}` : ""}
          </div>
          {claim.chunk_ids.length === 0 && <p className="muted">No citation.</p>}
          {claim.chunk_ids.map((id) =>
            answer.citations[id] ? (
              <CitationCard key={id} citation={answer.citations[id]} />
            ) : (
              <p key={id} className="muted">
                Unknown chunk {id}
              </p>
            ),
          )}
        </div>
      )}

      {answer.claims.length > 0 && (
        <details className="claims" open={answer.mode === "research"}>
          <summary>
            Evidence · {answer.claims.length} claims
            {unverified.length > 0 && <span className="status-unverified"> · {unverified.length} unverified</span>}
          </summary>
          <ul>
            {answer.claims.map((c) => (
              <li key={c.id}>
                <button className="marker" onClick={() => setSelected(c.id)}>
                  {c.id}
                </button>
                <span className={`badge status-${c.status}`}>{STATUS_LABEL[c.status]}</span>
                <span>{c.text}</span>
                {formatValue(c) && <span className="muted"> · {formatValue(c)}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="answer-foot muted small">
        {answer.model} · {answer.usage.latency_s.toFixed(1)} s ·{" "}
        {answer.usage.input_tokens.toLocaleString()} in / {answer.usage.output_tokens.toLocaleString()} out · $
        {answer.usage.cost_usd.toFixed(4)} ·{" "}
        <button className="link" onClick={onShowTrace}>
          View trace
        </button>
      </div>
    </section>
  );
}
