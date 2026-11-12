import { useEffect, useState } from 'react'
import type { Answer, Claim, SubRun, TraceEvent } from '../api'

const STATUS: Record<string, string> = {
  verified: 'bg-emerald-100 text-emerald-800 border-emerald-200',
  calculated: 'bg-sky-100 text-sky-800 border-sky-200',
  cited: 'bg-indigo-100 text-indigo-800 border-indigo-200',
  unverified: 'bg-amber-100 text-amber-900 border-amber-300',
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-block rounded border px-1.5 py-0.5 text-[11px] font-medium ${
      STATUS[status] ?? 'bg-stone-100 text-stone-700 border-stone-200'}`}>{status}</span>
  )
}

export function SourceChip({ id, onOpen }: { id: string; onOpen: (id: string) => void }) {
  return (
    <button onClick={() => onOpen(id)}
      className="rounded bg-stone-100 px-1.5 py-0.5 font-mono text-[11px] text-stone-700 hover:bg-stone-200">
      {id}
    </button>
  )
}

function ClaimRow({ claim, onSource }: { claim: Claim; onSource: (id: string) => void }) {
  const [open, setOpen] = useState(false)
  const value = claim.value != null ? `${claim.value}${claim.unit ? ` ${claim.unit}` : ''}` : null
  return (
    <li className="border-t border-stone-100 py-2 first:border-0">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 w-6 shrink-0 text-xs text-stone-400">[{String(claim.id)}]</span>
        <StatusBadge status={claim.status} />
        <div className="min-w-0 flex-1">
          <p className="text-sm">{claim.text}</p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-stone-500">
            {value && <span className="font-mono text-stone-700">= {value}</span>}
            {claim.company && <span>{claim.company}</span>}
            {(claim.period || claim.fiscal_year) && <span>{claim.period ?? `FY${claim.fiscal_year}`}</span>}
            {claim.chunk_ids.map((id) => <SourceChip key={id} id={id} onOpen={onSource} />)}
            <button className="text-stone-400 hover:text-stone-700" onClick={() => setOpen(!open)}>
              {open ? 'less' : 'details'}
            </button>
          </div>
          {open && (
            <pre className="mt-1 max-h-60 overflow-auto rounded bg-stone-50 p-2 text-[11px] text-stone-600">
              {JSON.stringify(claim, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </li>
  )
}

function Table({ table }: { table: NonNullable<Answer['table']> }) {
  const columns = (table.columns as string[] | undefined) ?? []
  const rows = (table.rows as unknown[][] | undefined) ?? []
  if (!columns.length && !rows.length) return null
  return (
    <div className="my-3 overflow-x-auto">
      {table.title && <div className="mb-1 text-xs font-medium text-stone-500">{String(table.title)}</div>}
      <table className="text-sm">
        <thead><tr>{columns.map((c) => <th key={c} className="border border-stone-200 bg-stone-100 px-2 py-1 text-left">{c}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={i}>{(Array.isArray(r) ? r : Object.values(r as object)).map((v, j) => (
            <td key={j} className="border border-stone-200 px-2 py-1">{String(v ?? '')}</td>))}</tr>
        ))}</tbody>
      </table>
    </div>
  )
}

export function AnswerBody({ answer, onSource }: { answer: Answer; onSource: (id: string) => void }) {
  const counts = answer.claims.reduce<Record<string, number>>((m, c) => ({ ...m, [c.status]: (m[c.status] ?? 0) + 1 }), {})
  return (
    <div>
      {answer.abstained && <div className="mb-2 text-xs font-medium text-amber-700">Declined: the filings can't answer this</div>}
      <p className="whitespace-pre-wrap leading-relaxed">{answer.answer}</p>
      {answer.table && <Table table={answer.table} />}
      {answer.scope_notice && <p className="mt-2 text-xs text-stone-500 italic">{answer.scope_notice}</p>}
      {answer.assumptions && answer.assumptions.length > 0 && (
        <div className="mt-3">
          <div className="text-xs font-semibold text-stone-500 uppercase">Assumptions</div>
          <ul className="list-disc pl-5 text-sm">
            {answer.assumptions.map((a, i) => <li key={i}>{typeof a === 'string' ? a : JSON.stringify(a)}</li>)}
          </ul>
        </div>
      )}
      {answer.claims.length > 0 && (
        <details className="group mt-3 rounded-2xl border border-neutral-200 bg-white/70 px-4 py-1">
          <summary className="flex cursor-pointer list-none items-center gap-2 py-2 text-xs text-neutral-500 select-none">
            <span className="text-neutral-400 transition-transform group-open:rotate-90">▸</span>
            <span className="font-semibold uppercase">Claims ({answer.claims.length})</span>
            {Object.entries(counts).map(([s, n]) => <span key={s}>{n} {s}</span>)}
          </summary>
          <ul className="pb-1">{answer.claims.map((c) => <ClaimRow key={String(c.id)} claim={c} onSource={onSource} />)}</ul>
        </details>
      )}
    </div>
  )
}

function short(v: unknown, n = 140) {
  const s = typeof v === 'string' ? v : JSON.stringify(v)
  return s && s.length > n ? s.slice(0, n) + '…' : s
}

export function Spinner({ className = 'h-4 w-4' }: { className?: string }) {
  return <span role="status" aria-label="working" className={`inline-block shrink-0 animate-spin rounded-full border-2 border-neutral-300 border-t-neutral-700 ${className}`} />
}

/** What the agent is doing now, from the latest trace event. */
export function activity(events: TraceEvent[]): string {
  const e = events[events.length - 1]
  if (!e) return 'Starting…'
  const sub = e.scope ? ` (${e.scope})` : ''
  switch (e.type) {
    case 'model_start': return `Model thinking${sub}…`
    case 'tool_start': return `Calling ${e.name}${sub}…`
    case 'tool': return `Got ${e.name} results${sub}, reading…`
    case 'model': return e.tool_calls?.length ? `Running ${e.tool_calls.join(', ')}${sub}…` : `Writing the answer${sub}…`
    case 'sub_start': return `Sub-question: ${short(e.question, 80)}`
    case 'sub_end': return 'Sub-question done, continuing…'
    case 'status': return `${e.text[0].toUpperCase()}${e.text.slice(1)}…`
    default: return 'Working…'
  }
}

export function LiveStatus({ events, since }: { events: TraceEvent[]; since?: number }) {
  const [start] = useState(() => since ?? Date.now())
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(id) }, [])
  const secs = Math.floor((now - start) / 1000)
  const tools = events.filter((e) => e.type === 'tool').length
  return (
    <div className="flex items-center gap-2 rounded-full bg-neutral-100 px-4 py-2 text-sm text-neutral-700">
      <Spinner />
      <span className="min-w-0 flex-1 truncate">{activity(events)}</span>
      <span className="shrink-0 font-mono text-xs text-neutral-500">{tools} tool calls · {Math.floor(secs / 60)}:{String(secs % 60).padStart(2, '0')}</span>
    </div>
  )
}

export function Trace({ events, live }: { events: TraceEvent[]; live?: boolean }) {
  const [open, setOpen] = useState<number | null>(null)
  const shown = events.filter((e) => ['model', 'tool', 'sub_start', 'sub_end', 'status'].includes(e.type))
  const pending = live ? [...events].reverse().find((e) => ['model_start', 'tool_start'].includes(e.type)) : undefined
  const last = events[events.length - 1]
  const running = pending && last === pending
  return (
    <ol className="space-y-1 font-mono text-[12px]">
      {shown.map((e, i) => (
        <li key={i} className={e.scope ? 'ml-4 border-l border-stone-200 pl-2' : ''}>
          <button className="w-full text-left hover:bg-stone-100" onClick={() => setOpen(open === i ? null : i)}>
            <span className="mr-2 text-stone-400">{e.t?.toFixed(1)}s</span>
            {e.type === 'tool' && <><span className="text-violet-700">{e.name}</span> <span className="text-stone-500">{short(e.arguments, 100)}</span> <span className="text-stone-400">{e.seconds}s</span></>}
            {e.type === 'model' && <><span className="text-teal-700">model</span> <span className="text-stone-500">{e.tool_calls?.length ? `→ ${e.tool_calls.join(', ')}` : short(e.text, 100) || 'answer'}</span> <span className="text-stone-400">{e.seconds}s · {e.input_tokens}/{e.output_tokens} tok</span></>}
            {e.type === 'sub_start' && <><span className="text-orange-700">sub-question [{e.mode}]</span> <span>{e.question}</span></>}
            {e.type === 'sub_end' && <><span className="text-orange-700">sub-answer</span> <span className="text-stone-500">{e.error ?? short(e.answer?.answer, 120)}</span></>}
            {e.type === 'status' && <span className="text-stone-500">{e.text}</span>}
          </button>
          {open === i && (
            <pre className="mt-1 max-h-80 overflow-auto rounded bg-stone-100 p-2 whitespace-pre-wrap text-[11px]">
              {e.type === 'model' ? e.text : JSON.stringify(e.type === 'tool' ? { arguments: e.arguments, result: e.result } : e, null, 2)}
            </pre>
          )}
        </li>
      ))}
      {live && (
        <li className={`flex items-center gap-2 text-stone-500 ${running && pending.scope ? 'ml-4 pl-2' : ''}`}>
          <Spinner className="h-3 w-3" />
          {running ? (pending.type === 'tool_start' ? `calling ${pending.name} ${short(pending.arguments, 80)}` : 'model thinking…') : 'working…'}
        </li>
      )}
    </ol>
  )
}

export function SubRuns({ subs, onSource }: { subs: SubRun[]; onSource: (id: string) => void }) {
  return (
    <div className="space-y-2">
      {subs.map((s) => (
        <details key={s.id} className="rounded border border-stone-200 bg-white px-3 py-2">
          <summary className="cursor-pointer text-sm">
            <span className="mr-2 font-mono text-xs text-stone-400">{s.id}</span>
            <span className="mr-2 rounded bg-stone-100 px-1.5 text-xs">{s.mode}</span>
            {s.follow_up && <span className="mr-2 text-xs text-orange-700">follow-up</span>}
            {s.question}
          </summary>
          <div className="mt-2">
            {s.why && <p className="mb-2 text-xs text-stone-500 italic">{s.why}</p>}
            {s.answer ? <AnswerBody answer={s.answer} onSource={onSource} /> : <p className="text-sm text-red-700">{s.stop}</p>}
            <p className="mt-1 text-xs text-stone-400">{s.calls} tool calls · {s.seconds}s · {s.stop}</p>
          </div>
        </details>
      ))}
    </div>
  )
}
