import { useEffect, useState } from 'react'
import { getJSON, postJSON, stream, today } from '../api'
import type { TraceEvent } from '../api'
import { Spinner } from './AnswerView'
import { NoteView } from './Readable'

interface ReportRow {
  ticker: string; as_of: string; version: string; rating: string; price_target: number
  exceptions: number; untraced: number; seconds: number | null; modified: number
}
interface Job {
  id: string; ticker: string; as_of: string; llm: boolean; status: string; error: string | null; created: number
}

const STAGES = ['S2 events', 'S3-S5 model and forecast', 'S6-S7 peers and valuation', 'S8 revisions',
  'S9 debates', 'S7 sensitivities', 'S10 write', 'S11 export']

/** Epoch seconds → "Nov 12, 2026, 2:32 PM" in the viewer's locale. */
function when(epoch: number) {
  return new Date(epoch * 1000).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })
}

function clock(secs: number) {
  const s = Math.max(0, Math.floor(secs))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function JobCard({ job, onDone, onDismiss }: { job: Job; onDone: () => void; onDismiss: () => void }) {
  const [events, setEvents] = useState<TraceEvent[]>([])
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const ctrl = new AbortController()
    setEvents([])
    stream(`/api/jobs/${job.id}/events`, {}, (e) => {
      if (e.type === 'ping') return
      setEvents((es) => [...es, e])
      if (e.type === 'done' || e.type === 'error' || e.type === 'cancelled') onDone()
    }, ctrl.signal).catch(() => {})
    return () => ctrl.abort()
  }, [job.id])
  const reached = events.filter((e) => e.type === 'stage').map((e) => e.name)
  const failed = events.find((e) => e.type === 'error')
  const cancelled = events.some((e) => e.type === 'cancelled')
  const done = events.some((e) => e.type === 'done')
  const ended = !!failed || cancelled || done
  useEffect(() => {
    if (ended) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [ended])
  const status = [...events].reverse().find((e) => e.type === 'status')
  const llmCalls = events.filter((e) => e.type === 'llm').length
  const current = reached[reached.length - 1]
  const label = failed ? 'Failed' : cancelled ? 'Stopped' : done ? 'Done'
    : current ? `${current}${llmCalls ? ' · writing with the model' : ''}…`
    : `${(status?.text ?? 'queued').replace(/^./, (c: string) => c.toUpperCase())}…`
  const lastT = events[events.length - 1]?.t ?? job.created
  const elapsed = (ended ? lastT : now / 1000) - job.created
  return (
    <div className="rounded-3xl border border-neutral-200 bg-white p-5 shadow-[0_2px_12px_rgba(0,0,0,0.05)]">
      <div className="flex items-center gap-3">
        {!ended && <Spinner />}
        <div className="min-w-0 flex-1">
          <div className="font-medium">{job.ticker} <span className="font-normal text-neutral-500">· as of {job.as_of} · {job.llm ? 'full' : 'code only'}</span></div>
          <div className={`truncate text-sm ${failed ? 'text-red-700' : 'text-neutral-500'}`}>{label}</div>
        </div>
        <span className="shrink-0 font-mono text-xs text-neutral-500">{llmCalls} LLM calls · {clock(elapsed)}</span>
        {!ended ? (
          <button onClick={() => postJSON(`/api/jobs/${job.id}/cancel`, {})} title="Stop this run (after the current step)"
            className="flex h-9 w-9 items-center justify-center rounded-full bg-neutral-800 hover:bg-neutral-900">
            <span className="h-3 w-3 rounded-[2px] bg-white" /></button>
        ) : (
          <button onClick={onDismiss} title="Dismiss" className="h-9 w-9 rounded-full text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700">✕</button>
        )}
      </div>
      <ol className="mt-4 flex flex-wrap gap-1.5">
        {STAGES.map((s) => {
          const i = reached.indexOf(s)
          const now = i >= 0 && i === reached.length - 1 && !ended
          return <li key={s} className={`rounded-full px-3 py-1 text-xs ${
            now ? 'animate-pulse bg-neutral-800 text-white' : i >= 0 ? 'bg-neutral-200 text-neutral-700' : 'bg-neutral-50 text-neutral-400'}`}>
            {i >= 0 && !now ? '✓ ' : ''}{s}</li>
        })}
      </ol>
      {failed && <p className="mt-3 text-sm text-red-700">{failed.text}</p>}
      {cancelled && <p className="mt-3 text-sm text-neutral-500">Stopped. Run it again with <b>Resume</b> to reuse the LLM steps already done.</p>}
    </div>
  )
}

function ReportDetail({ ticker, asOf, onBack }: { ticker: string; asOf: string; onBack: () => void }) {
  const [data, setData] = useState<any>(null)
  const [tab, setTab] = useState<'note' | 'exceptions' | 'revisions' | 'summary'>('note')
  const [by, setBy] = useState('')
  const [msg, setMsg] = useState('')
  useEffect(() => { getJSON(`/api/reports/${ticker}/${asOf}`).then(setData) }, [ticker, asOf])
  if (!data) return <div className="flex items-center gap-2 p-10 text-neutral-500"><Spinner /> Loading…</div>
  const s = data.summary
  const version = data.versions.find((v: any) => v.id === s.version)
  const parentAsOf = s.parent ? data.versions.find((v: any) => v.id === s.parent)?.as_of ?? 'earlier' : null
  async function approve() {
    try {
      const r = await postJSON<any>('/api/reports/approve', { version: s.version, by })
      setData({ ...data, versions: r.versions }); setMsg('Approved')
    } catch (e) { setMsg(String(e)) }
  }
  const base = `/api/reports/${ticker}/${asOf}/files/`
  return (
    <div className="mx-auto max-w-4xl px-4 pt-6 pb-10">
      <button onClick={onBack} className="mb-4 rounded-full px-3 py-1 text-sm text-neutral-600 hover:bg-neutral-100">← All reports</button>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-glow text-4xl font-medium tracking-tight">{ticker}</h2>
          <p className="mt-1 text-sm text-neutral-500">
            as of {asOf}
            {data.generated && <> · generated {when(data.generated)}</>}
            {parentAsOf && <> · revises the {parentAsOf} report</>}
            {' · '}<span className="capitalize">{version?.status ?? 'draft'}</span>
            {s.untraced > 0 && <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800">{s.untraced} untraced figures</span>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {data.files.map((f: string) => <a key={f} href={base + f} className="rounded-full border border-neutral-200 bg-white px-3 py-1 text-sm text-neutral-700 hover:bg-neutral-100">⤓ {f}</a>)}
          <input value={by} onChange={(e) => setBy(e.target.value)} placeholder="initials" title="Your initials, recorded as the reviewer" className="w-24 rounded-full border border-neutral-200 bg-white px-3 py-1 text-sm focus:outline-none" />
          <button disabled={!by} onClick={approve} title="Mark this report as reviewed" className="rounded-full bg-neutral-800 px-4 py-1 text-sm text-white hover:bg-neutral-900 disabled:bg-neutral-200 disabled:text-neutral-400">Approve report</button>
          {msg && <span className="text-xs text-neutral-500">{msg}</span>}
        </div>
      </div>
      <div className="mb-4 inline-flex rounded-full bg-neutral-100 p-0.5">
        {(['note', 'exceptions', 'revisions', 'summary'] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`rounded-full px-4 py-1 text-sm capitalize ${tab === t ? 'bg-neutral-800 text-white shadow' : 'text-neutral-600 hover:text-neutral-900'}`}>
            {t}{t === 'exceptions' ? ` (${data.exceptions.length})` : ''}</button>
        ))}
      </div>
      {tab === 'note' && <NoteView note={data.note.replace(/the prior version \(v(\d+)\)/g, (_: string, n: string) => {
        const v = data.versions.find((x: any) => x.id === `${ticker}:v${n}`)
        return v ? `the ${v.as_of} report` : 'the prior report'
      })} />}
      {tab === 'exceptions' && (
        <div className="overflow-hidden rounded-2xl border border-neutral-200 bg-white"><table className="w-full text-sm">
          <thead><tr className="bg-neutral-50 text-left text-xs text-neutral-500"><th className="px-3 py-2">id</th><th className="px-3 py-2">severity</th><th className="px-3 py-2">item</th></tr></thead>
          <tbody>{data.exceptions.map((x: any) => (
            <tr key={x.id} className="border-t border-neutral-100 align-top">
              <td className="px-3 py-2 font-mono text-xs">{x.id}</td>
              <td className={`px-3 py-2 text-xs ${x.severity === 'error' ? 'text-red-700' : x.severity === 'warning' ? 'text-amber-700' : 'text-neutral-500'}`}>{x.severity}</td>
              <td className="px-3 py-2">{x.item}</td></tr>))}</tbody>
        </table></div>
      )}
      {tab === 'revisions' && <pre className="overflow-auto rounded-2xl border border-neutral-200 bg-white p-4 text-xs">{JSON.stringify(data.revisions, null, 2)}</pre>}
      {tab === 'summary' && <pre className="overflow-auto rounded-2xl border border-neutral-200 bg-white p-4 text-xs">{JSON.stringify({ summary: s, versions: data.versions }, null, 2)}</pre>}
    </div>
  )
}

export function ReportsView({ companies }: { companies: { ticker: string; name: string }[] }) {
  const [rows, setRows] = useState<ReportRow[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [open, setOpen] = useState<{ ticker: string; asOf: string } | null>(null)
  const [ticker, setTicker] = useState('')
  const [asOf, setAsOf] = useState(today())
  const [llm, setLlm] = useState(true)
  const [resume, setResume] = useState(false)
  const [error, setError] = useState('')

  const load = () => getJSON<{ reports: ReportRow[]; jobs: Job[] }>('/api/reports').then((r) => { setRows(r.reports); setJobs(r.jobs) })
  useEffect(() => { load() }, [])
  useEffect(() => { if (!ticker && companies.length) setTicker(companies[0].ticker) }, [companies])

  async function start() {
    setError('')
    try { await postJSON('/api/reports', { ticker, as_of: asOf, llm, resume: llm && resume }); load() } catch (e) { setError(String(e)) }
  }

  if (open) return <ReportDetail ticker={open.ticker} asOf={open.asOf} onBack={() => { setOpen(null); load() }} />
  const active = jobs.filter((j) => j.status !== 'done')
  async function dismiss(id: string) {
    setJobs((js) => js.filter((j) => j.id !== id))
    await fetch(`/api/jobs/${id}`, { method: 'DELETE' }).catch(() => {})
  }
  const chip = (on: boolean) => `rounded-full px-3 py-1 text-sm ${on ? 'bg-neutral-800 text-white shadow' : 'text-neutral-600 hover:text-neutral-900'}`
  return (
    <div className="mx-auto max-w-4xl space-y-8 px-4 pt-6 pb-10">
      <div>
        <h2 className="text-glow text-4xl font-medium tracking-tight">Reports</h2>
      </div>

      <section className="rounded-[28px] border border-neutral-200 bg-white p-4 shadow-[0_2px_12px_rgba(0,0,0,0.06)]">
        <div className="flex flex-wrap items-center gap-2">
          <select value={ticker} onChange={(e) => setTicker(e.target.value)}
            className="rounded-full bg-neutral-100 px-3 py-1.5 text-sm focus:outline-none">
            {companies.map((c) => <option key={c.ticker} value={c.ticker}>{c.ticker} · {c.name}</option>)}
          </select>
          <label className="flex items-center gap-1 rounded-full bg-neutral-100 px-3 py-1 text-sm text-neutral-600">
            as of <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} className="bg-transparent focus:outline-none" />
          </label>
          <div className="flex rounded-full bg-neutral-100 p-0.5">
            <button onClick={() => setLlm(true)} className={chip(llm)} title="Every step, including the ones the model writes (needs the model server)">Full</button>
            <button onClick={() => setLlm(false)} className={chip(!llm)} title="Numbers, model and workbook only; no model server needed">Code only</button>
          </div>
          <button onClick={() => setResume(!resume)} disabled={!llm}
            title="Reuse the LLM steps saved by an earlier, unfinished run of this report"
            className={`rounded-full border px-3 py-1 text-sm disabled:opacity-40 ${resume && llm ? 'border-neutral-800 bg-neutral-800 text-white' : 'border-neutral-200 text-neutral-600 hover:bg-neutral-100'}`}>
            {resume && llm ? '✓ ' : ''}Resume</button>
          <button onClick={start} disabled={!ticker}
            className="ml-auto rounded-full bg-neutral-800 px-5 py-2 text-sm text-white hover:bg-neutral-900 disabled:bg-neutral-200">Generate ➤</button>
        </div>
        <p className="mt-3 px-1 text-xs text-neutral-500">
          {llm ? <><b>Full</b>: every step, with the model writing the debates and the note (needs the model server, up to ~30 min).</>
            : <><b>Code only</b>: numbers, model, valuation and workbook, with draft text; no model server, a few minutes.</>}
          {llm && <> <b>Resume</b>{resume ? ' on' : ''}: reuse the LLM steps saved by an earlier stopped or crashed run.</>}
        </p>
        {error && <p className="mt-2 px-1 text-sm text-red-700">{error}</p>}
      </section>

      {active.length > 0 && <section className="space-y-3">{active.map((j) => (
        <JobCard key={j.id} job={j} onDone={load} onDismiss={() => dismiss(j.id)} />))}</section>}

      <section>
        <h3 className="mb-3 px-1 text-sm font-medium text-neutral-600">Saved reports ({rows.length})</h3>
        <div className="overflow-hidden rounded-3xl border border-neutral-200 bg-white">
          <table className="w-full text-sm">
            <thead><tr className="bg-neutral-50 text-left text-xs text-neutral-500">
              {['Ticker', 'As of', 'Generated', 'Rating', 'PT', 'Exceptions', 'Untraced', 'Run time'].map((h) => <th key={h} className="px-4 py-2.5 font-medium">{h}</th>)}</tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={`${r.ticker}/${r.as_of}`} onClick={() => setOpen({ ticker: r.ticker, asOf: r.as_of })} className="cursor-pointer border-t border-neutral-100 hover:bg-neutral-50">
                <td className="px-4 py-3 font-medium">{r.ticker}</td><td className="px-4 py-3 text-neutral-600">{r.as_of}</td>
                <td className="px-4 py-3 text-neutral-600">{when(r.modified)}</td>
                <td className="px-4 py-3"><span className="rounded-full bg-neutral-100 px-2.5 py-0.5 text-xs">{r.rating}</span></td>
                <td className="px-4 py-3">${r.price_target?.toFixed(2)}</td><td className="px-4 py-3 text-neutral-600">{r.exceptions}</td>
                <td className="px-4 py-3 text-neutral-600">{r.untraced}</td>
                <td className="px-4 py-3 text-neutral-600">{r.seconds != null ? `${Math.round(r.seconds / 60)} min` : ''}</td>
              </tr>))}</tbody>
          </table>
          {rows.length === 0 && <p className="p-6 text-center text-sm text-neutral-400">No reports yet.</p>}
        </div>
      </section>
    </div>
  )
}
