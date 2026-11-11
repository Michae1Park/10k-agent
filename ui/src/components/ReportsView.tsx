import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { getJSON, postJSON, stream, today } from '../api'
import type { TraceEvent } from '../api'

interface ReportRow {
  ticker: string; as_of: string; version: string; rating: string; price_target: number
  exceptions: number; untraced: number; seconds: number | null; modified: number
}
interface Job { id: string; ticker: string; as_of: string; llm: boolean; status: string; error: string | null }

const STAGES = ['S2 events', 'S3-S5 model and forecast', 'S6-S7 peers and valuation', 'S8 revisions',
  'S9 debates', 'S7 sensitivities', 'S10 write', 'S11 export']

function JobProgress({ job, onDone }: { job: Job; onDone: () => void }) {
  const [events, setEvents] = useState<TraceEvent[]>([])
  useEffect(() => {
    const ctrl = new AbortController()
    setEvents([])
    stream(`/api/jobs/${job.id}/events`, {}, (e) => {
      if (e.type === 'ping') return
      setEvents((es) => [...es, e])
      if (e.type === 'done' || e.type === 'error') onDone()
    }, ctrl.signal).catch(() => {})
    return () => ctrl.abort()
  }, [job.id])
  const reached = events.filter((e) => e.type === 'stage').map((e) => e.name)
  const failed = events.find((e) => e.type === 'error')
  const done = events.some((e) => e.type === 'done')
  const status = [...events].reverse().find((e) => e.type === 'status')
  return (
    <div className="rounded-lg border border-stone-200 bg-white p-3">
      <div className="mb-2 text-sm font-medium">{job.ticker} · as of {job.as_of}{job.llm ? '' : ' · code only'}
        <span className="ml-2 text-xs text-stone-500">{failed ? 'failed' : done ? 'done' : status?.text ?? job.status}</span></div>
      <ol className="flex flex-wrap gap-1">
        {STAGES.map((s) => {
          const i = reached.indexOf(s)
          const current = i >= 0 && i === reached.length - 1 && !done && !failed
          return <li key={s} className={`rounded px-2 py-0.5 text-[11px] ${
            current ? 'animate-pulse bg-sky-600 text-white' : i >= 0 ? 'bg-emerald-100 text-emerald-800' : 'bg-stone-100 text-stone-400'}`}>{s}</li>
        })}
      </ol>
      {failed && <p className="mt-2 text-sm text-red-700">{failed.text}</p>}
    </div>
  )
}

function ReportDetail({ ticker, asOf, onBack }: { ticker: string; asOf: string; onBack: () => void }) {
  const [data, setData] = useState<any>(null)
  const [tab, setTab] = useState<'note' | 'exceptions' | 'revisions' | 'summary'>('note')
  const [by, setBy] = useState('')
  const [msg, setMsg] = useState('')
  useEffect(() => { getJSON(`/api/reports/${ticker}/${asOf}`).then(setData) }, [ticker, asOf])
  if (!data) return <p className="p-6 text-stone-400">Loading…</p>
  const s = data.summary
  const version = data.versions.find((v: any) => v.id === s.version)
  async function approve() {
    try {
      const r = await postJSON<any>('/api/reports/approve', { version: s.version, by })
      setData({ ...data, versions: r.versions }); setMsg('Approved')
    } catch (e) { setMsg(String(e)) }
  }
  const base = `/api/reports/${ticker}/${asOf}/files/`
  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <button onClick={onBack} className="mb-3 text-sm text-stone-500 hover:text-stone-900">← All reports</button>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold">{ticker} <span className="text-stone-400">· as of {asOf}</span></h2>
          <p className="text-sm text-stone-600">{s.version} · {s.rating} · PT ${s.price_target?.toFixed(2)} · {s.exceptions} exceptions · {s.untraced} untraced
            {s.parent ? ` · parent ${s.parent}` : ''}</p>
          <p className="text-xs text-stone-500">Status: {version?.status ?? 'draft'}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {data.files.map((f: string) => <a key={f} href={base + f} className="rounded border border-stone-300 bg-white px-3 py-1 text-sm hover:bg-stone-100">{f}</a>)}
          <input value={by} onChange={(e) => setBy(e.target.value)} placeholder="initials" className="w-20 rounded border border-stone-300 px-2 py-1 text-sm" />
          <button disabled={!by} onClick={approve} className="rounded bg-emerald-700 px-3 py-1 text-sm text-white disabled:opacity-40">Approve {s.version}</button>
          {msg && <span className="text-xs text-stone-500">{msg}</span>}
        </div>
      </div>
      <div className="mb-3 flex gap-1 border-b border-stone-200">
        {(['note', 'exceptions', 'revisions', 'summary'] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`-mb-px border-b-2 px-3 py-1.5 text-sm capitalize ${tab === t ? 'border-stone-900 font-medium' : 'border-transparent text-stone-500'}`}>
            {t}{t === 'exceptions' ? ` (${data.exceptions.length})` : ''}</button>
        ))}
      </div>
      {tab === 'note' && <article className="prose-note rounded-lg border border-stone-200 bg-white p-6"><ReactMarkdown remarkPlugins={[remarkGfm]}>{data.note}</ReactMarkdown></article>}
      {tab === 'exceptions' && (
        <table className="w-full bg-white text-sm">
          <thead><tr className="bg-stone-100 text-left"><th className="px-2 py-1">id</th><th className="px-2 py-1">severity</th><th className="px-2 py-1">item</th></tr></thead>
          <tbody>{data.exceptions.map((x: any) => (
            <tr key={x.id} className="border-t border-stone-100 align-top">
              <td className="px-2 py-1 font-mono text-xs">{x.id}</td>
              <td className={`px-2 py-1 text-xs ${x.severity === 'error' ? 'text-red-700' : x.severity === 'warning' ? 'text-amber-700' : 'text-stone-500'}`}>{x.severity}</td>
              <td className="px-2 py-1">{x.item}</td></tr>))}</tbody>
        </table>
      )}
      {tab === 'revisions' && <pre className="overflow-auto rounded bg-white p-3 text-xs">{JSON.stringify(data.revisions, null, 2)}</pre>}
      {tab === 'summary' && <pre className="overflow-auto rounded bg-white p-3 text-xs">{JSON.stringify({ summary: s, versions: data.versions }, null, 2)}</pre>}
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
    try { await postJSON('/api/reports', { ticker, as_of: asOf, llm, resume }); load() } catch (e) { setError(String(e)) }
  }

  if (open) return <ReportDetail ticker={open.ticker} asOf={open.asOf} onBack={() => { setOpen(null); load() }} />
  const active = jobs.filter((j) => j.status === 'queued' || j.status === 'running' || j.status === 'failed')
  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6">
      <section className="rounded-xl border border-stone-200 bg-white p-4">
        <h2 className="mb-1 font-semibold">Generate a report</h2>
        <p className="mb-3 text-xs text-stone-500">The fixed S1–S11 workflow: data pack, model, forecast, valuation, debates, note, workbook and evidence log.</p>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <select value={ticker} onChange={(e) => setTicker(e.target.value)} className="rounded border border-stone-300 px-2 py-1">
            {companies.map((c) => <option key={c.ticker} value={c.ticker}>{c.ticker} · {c.name}</option>)}
          </select>
          <label className="flex items-center gap-1">as of <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} className="rounded border border-stone-300 px-2 py-0.5" /></label>
          <label className="flex items-center gap-1"><input type="checkbox" checked={llm} onChange={(e) => setLlm(e.target.checked)} /> LLM steps</label>
          <label className="flex items-center gap-1"><input type="checkbox" checked={resume} onChange={(e) => setResume(e.target.checked)} /> resume</label>
          <button onClick={start} className="rounded bg-stone-900 px-4 py-1 text-white">Run</button>
          {error && <span className="text-red-700">{error}</span>}
        </div>
      </section>
      {active.length > 0 && <section className="space-y-2">{active.map((j) => <JobProgress key={j.id} job={j} onDone={load} />)}</section>}
      <section>
        <h2 className="mb-2 font-semibold">Reports ({rows.length})</h2>
        <table className="w-full overflow-hidden rounded-lg border border-stone-200 bg-white text-sm">
          <thead><tr className="bg-stone-100 text-left text-xs text-stone-600">
            {['Ticker', 'As of', 'Version', 'Rating', 'PT', 'Exceptions', 'Untraced', 'Run time'].map((h) => <th key={h} className="px-3 py-2">{h}</th>)}</tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={`${r.ticker}/${r.as_of}`} onClick={() => setOpen({ ticker: r.ticker, asOf: r.as_of })} className="cursor-pointer border-t border-stone-100 hover:bg-stone-50">
              <td className="px-3 py-2 font-medium">{r.ticker}</td><td className="px-3 py-2">{r.as_of}</td>
              <td className="px-3 py-2 font-mono text-xs">{r.version}</td><td className="px-3 py-2">{r.rating}</td>
              <td className="px-3 py-2">${r.price_target?.toFixed(2)}</td><td className="px-3 py-2">{r.exceptions}</td>
              <td className="px-3 py-2">{r.untraced}</td><td className="px-3 py-2">{r.seconds != null ? `${Math.round(r.seconds / 60)} min` : ''}</td>
            </tr>))}</tbody>
        </table>
      </section>
    </div>
  )
}
