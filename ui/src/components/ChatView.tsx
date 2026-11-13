import { useEffect, useRef, useState } from 'react'
import { MODES, getJSON, stream, today } from '../api'
import type { AssistantMessage, History, Message, Mode, TraceEvent } from '../api'
import { AnswerBody, LiveStatus, SubRuns, Trace } from './AnswerView'

const MODE_STYLE: Record<Mode, string> = {
  ask: 'bg-neutral-800', research: 'bg-neutral-800', investigate: 'bg-neutral-800',
}

function Assistant({ m, live, onSource }: { m: AssistantMessage; live?: boolean; onSource: (id: string) => void }) {
  const [showTrace, setShowTrace] = useState(!!live)
  useEffect(() => { if (live) setShowTrace(true) }, [live])
  const trace = m.trace ?? []
  const tools = trace.filter((e) => e.type === 'tool').length
  return (
    <div className="flex gap-4">
      <div className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-neutral-300 to-neutral-700 text-sm text-white ${live ? 'animate-pulse' : ''}`}>✦</div>
      <div className="min-w-0 flex-1">
      {live && !m.answer && !m.error && <div className="mb-3"><LiveStatus events={trace} since={m.started} /></div>}
      {m.rewritten && <p className="mb-2 text-xs text-stone-500">Interpreted as: <span className="italic">{m.rewritten}</span></p>}
      {m.error && <p className="text-sm text-red-700">{m.error}</p>}
      {m.answer && <AnswerBody answer={m.answer} onSource={onSource} />}
      {m.investigation && m.investigation.sub_runs.length > 0 && (
        <div className="mt-4">
          <div className="mb-1 text-xs font-semibold text-stone-500 uppercase">
            Plan · {m.investigation.sub_runs.length} sub-questions{m.investigation.fallback ? ' (fallback: one Research run)' : ''}
          </div>
          <SubRuns subs={m.investigation.sub_runs} onSource={onSource} />
          {m.investigation.log.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-xs text-stone-500">{m.investigation.log.map((l, i) => <li key={i}>{l}</li>)}</ul>
          )}
        </div>
      )}
      <div className="mt-3">
        <button onClick={() => setShowTrace(!showTrace)} className="text-xs text-stone-500 hover:text-stone-800">
          {showTrace ? '▾' : '▸'} Trace · {tools} tool calls{m.seconds != null ? ` · ${m.seconds.toFixed(1)}s` : ''}{m.stop ? ` · ${m.stop}` : ''}
        </button>
        {showTrace && <div className="mt-2 max-h-[28rem] overflow-auto rounded-2xl bg-neutral-50 p-3"><Trace events={trace} live={live} /></div>}
      </div>
      </div>
    </div>
  )
}

export function ChatView({ chatId, onChat, onSource, companies }: {
  chatId: string | null
  onChat: (id: string) => void
  onSource: (id: string) => void
  companies: { ticker: string; name: string }[]
}) {
  const [messages, setMessages] = useState<Message[]>([])
  const [mode, setMode] = useState<Mode>('ask')
  const [asOf, setAsOf] = useState(today())
  const [history, setHistory] = useState<History>('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [live, setLive] = useState<AssistantMessage | null>(null)
  const current = useRef<string | null>(chatId)
  const end = useRef<HTMLDivElement>(null)
  const abort = useRef<AbortController | null>(null)

  // Leaving the view only stops listening; the run goes on in the server and is re-attached on return.
  useEffect(() => () => abort.current?.abort(), [])

  /** Follow a run's event stream into `live`; resolves with the final message (null if we left). */
  async function follow(url: string, init: RequestInit, start: AssistantMessage): Promise<AssistantMessage | null> {
    abort.current?.abort()
    const ctrl = new AbortController()
    abort.current = ctrl
    let msg = start
    setLive(msg); setBusy(true)
    try {
      await stream(url, init, (e: TraceEvent) => {
        if (e.type === 'ping') return
        if (e.type === 'chat') {
          if (current.current !== e.chat_id) { current.current = e.chat_id; onChat(e.chat_id) }
          return
        }
        if (e.type === 'done' || e.type === 'error') { msg = e.message; return }
        msg = { ...msg, trace: [...(msg.trace ?? []), e] }
        setLive(msg)
      }, ctrl.signal)
    } catch (err) {
      if (ctrl.signal.aborted) return null
      msg = { ...msg, error: String(err) }
    }
    if (ctrl.signal.aborted) return null
    setLive(null); setBusy(false)
    if (current.current) onChat(current.current)  // refresh the sidebar's running marks
    return msg
  }

  async function load(id: string) {
    const c = await getJSON<{ messages: Message[]; running: number | null }>(`/api/chats/${id}`)
    if (current.current !== id) return
    setMessages(c.messages)
    const lastUser = [...c.messages].reverse().find((m) => m.role === 'user')
    if (lastUser && lastUser.role === 'user') { setMode(lastUser.mode); setAsOf(lastUser.as_of) }
    if (c.running != null) {
      const msg = await follow(`/api/chats/${id}/live`, {},
        { role: 'assistant', mode: lastUser?.mode ?? 'ask', trace: [], started: Date.now() - c.running * 1000 })
      if (msg && current.current === id) load(id)  // the saved reply
    }
  }

  useEffect(() => {
    if (chatId === current.current && messages.length) return
    abort.current?.abort()
    current.current = chatId
    setLive(null); setBusy(false)
    if (!chatId) { setMessages([]); return }
    load(chatId).catch(() => {})
  }, [chatId])

  async function cancel() {
    if (current.current) await fetch(`/api/chats/${current.current}/cancel`, { method: 'POST' })
  }

  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages.length, live?.trace?.length, live?.answer])

  async function send() {
    const question = text.trim()
    if (!question || busy) return
    setText('')
    setMessages((ms) => [...ms, { role: 'user', mode, as_of: asOf, text: question }])
    const msg = await follow('/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: current.current, mode, question, as_of: asOf, history: history || null }),
    }, { role: 'assistant', mode, trace: [], started: Date.now() })
    if (msg) setMessages((ms) => [...ms, msg])
  }

  const hint = MODES.find((m) => m.id === mode)!.hint
  const composer = (
          <div className="rounded-[28px] border border-neutral-200 bg-white px-4 pt-3 pb-2 shadow-[0_2px_12px_rgba(0,0,0,0.06)] focus-within:shadow-[0_4px_20px_rgba(0,0,0,0.10)]">
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
            placeholder={busy ? 'Running…' : `Ask Equity Research Agent (${MODES.find((m) => m.id === mode)!.label})`}
            className="w-full resize-none bg-transparent px-1 text-[15px] placeholder:text-neutral-400 focus:outline-none" />
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex rounded-full bg-neutral-100 p-0.5">
              {MODES.map((m) => (
                <button key={m.id} onClick={() => setMode(m.id)} title={m.hint}
                  className={`rounded-full px-3 py-1 text-sm ${mode === m.id ? `${MODE_STYLE[m.id]} text-white shadow` : 'text-stone-600 hover:text-stone-900'}`}>
                  {m.label}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-1 rounded-full bg-neutral-100 px-3 py-1 text-sm text-neutral-600">
              as of
              <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)}
                className="bg-transparent text-sm focus:outline-none" />
            </label>
            <label className="flex items-center gap-1 rounded-full bg-neutral-100 px-3 py-1 text-sm text-neutral-600"
              title="How a follow-up uses the earlier turns: append them to the agent's context, rewrite it into a standalone question, or ignore them">
              follow-ups
              <select value={history} onChange={(e) => setHistory(e.target.value as History)}
                className="bg-transparent text-sm focus:outline-none">
                <option value="">default</option>
                <option value="append">append</option>
                <option value="rewrite">rewrite</option>
                <option value="none">none</option>
              </select>
            </label>
            <span className="hidden min-w-0 flex-1 truncate text-xs text-neutral-400 md:inline">{hint}</span>
            {busy ? (
              <button onClick={cancel} title="Stop"
                className="ml-auto flex h-10 w-10 items-center justify-center rounded-full bg-neutral-800 text-white hover:bg-neutral-900">
                <span className="h-3.5 w-3.5 rounded-[3px] bg-white" /></button>
            ) : (
              <button onClick={send} disabled={!text.trim()} title="Send"
                className="ml-auto flex h-10 w-10 items-center justify-center rounded-full bg-neutral-800 text-lg text-white hover:bg-neutral-900 disabled:bg-neutral-200 disabled:text-neutral-400">➤</button>
            )}
          </div>
          </div>
  )
  const disclaimer = <p className="mt-2 text-center text-xs text-neutral-400">Answers are drafted by an agent from filings and can be wrong; check the cited sources.</p>

  if (messages.length === 0 && !live) return (
    <div className="flex h-full flex-col items-center justify-center overflow-auto px-4 pb-[8vh]">
      <div className="w-full max-w-3xl">
        <h2 className="text-glow text-5xl font-medium tracking-tight">Hello.</h2>
        <p className="mt-2 text-4xl font-medium tracking-tight text-neutral-300">What should we research today?</p>
        <div className="mt-10">{composer}</div>
        <p className="mt-4 text-center text-sm text-neutral-500">{companies.map((c) => c.ticker).join(' · ')}</p>
      </div>
    </div>
  )

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-3xl space-y-8 px-4 pt-8 pb-6">
          {messages.map((m, i) => m.role === 'user' ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[80%] rounded-3xl rounded-tr-md bg-neutral-100 px-5 py-3 text-neutral-900">
                <div className="mb-0.5 text-[11px] text-neutral-500">{m.mode} · as of {m.as_of}</div>
                <p className="whitespace-pre-wrap">{m.text}</p>
              </div>
            </div>
          ) : <Assistant key={i} m={m} onSource={onSource} />)}
          {live && <Assistant m={live} live onSource={onSource} />}
          <div ref={end} />
        </div>
      </div>
      <div>
        <div className="mx-auto max-w-3xl px-4 pb-4">
          {composer}
          {disclaimer}
        </div>
      </div>
    </div>
  )
}
