import { useEffect, useRef, useState } from 'react'
import { MODES, getJSON, stream, today } from '../api'
import type { AssistantMessage, Message, Mode, TraceEvent } from '../api'
import { AnswerBody, LiveStatus, SubRuns, Trace } from './AnswerView'

const MODE_STYLE: Record<Mode, string> = {
  ask: 'bg-emerald-600', research: 'bg-sky-600', investigate: 'bg-violet-600',
}

function Assistant({ m, live, onSource }: { m: AssistantMessage; live?: boolean; onSource: (id: string) => void }) {
  const [showTrace, setShowTrace] = useState(!!live)
  useEffect(() => { if (live) setShowTrace(true) }, [live])
  const trace = m.trace ?? []
  const tools = trace.filter((e) => e.type === 'tool').length
  return (
    <div className="rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
      {live && !m.answer && !m.error && <div className="mb-3"><LiveStatus events={trace} /></div>}
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
      <div className="mt-3 border-t border-stone-100 pt-2">
        <button onClick={() => setShowTrace(!showTrace)} className="text-xs text-stone-500 hover:text-stone-800">
          {showTrace ? '▾' : '▸'} Trace · {tools} tool calls{m.seconds != null ? ` · ${m.seconds.toFixed(1)}s` : ''}{m.stop ? ` · ${m.stop}` : ''}
        </button>
        {showTrace && <div className="mt-2 max-h-[28rem] overflow-auto"><Trace events={trace} live={live} /></div>}
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
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [live, setLive] = useState<AssistantMessage | null>(null)
  const current = useRef<string | null>(chatId)
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (chatId === current.current && messages.length) return
    current.current = chatId
    setLive(null)
    if (!chatId) { setMessages([]); return }
    getJSON<{ messages: Message[] }>(`/api/chats/${chatId}`).then((c) => {
      setMessages(c.messages)
      const lastUser = [...c.messages].reverse().find((m) => m.role === 'user')
      if (lastUser && lastUser.role === 'user') { setMode(lastUser.mode); setAsOf(lastUser.as_of) }
    })
  }, [chatId])

  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages.length, live?.trace?.length, live?.answer])

  async function send() {
    const question = text.trim()
    if (!question || busy) return
    setText(''); setBusy(true)
    setMessages((ms) => [...ms, { role: 'user', mode, as_of: asOf, text: question }])
    let msg: AssistantMessage = { role: 'assistant', mode, trace: [] }
    setLive(msg)
    try {
      await stream('/api/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: current.current, mode, question, as_of: asOf }),
      }, (e: TraceEvent) => {
        if (e.type === 'chat') {
          if (current.current !== e.chat_id) { current.current = e.chat_id; onChat(e.chat_id) }
          return
        }
        if (e.type === 'done' || e.type === 'error') { msg = e.message; return }
        msg = { ...msg, trace: [...(msg.trace ?? []), e] }
        setLive(msg)
      })
    } catch (err) {
      msg = { ...msg, error: String(err) }
    }
    setMessages((ms) => [...ms, msg])
    setLive(null); setBusy(false)
  }

  const hint = MODES.find((m) => m.id === mode)!.hint
  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-3xl space-y-4 px-4 py-6">
          {messages.length === 0 && !live && (
            <div className="py-16 text-center text-stone-500">
              <h2 className="mb-2 text-2xl font-semibold text-stone-800">Ask about the coverage</h2>
              <p className="text-sm">{companies.map((c) => c.ticker).join(' · ')}</p>
            </div>
          )}
          {messages.map((m, i) => m.role === 'user' ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[85%] rounded-xl bg-stone-800 px-4 py-2 text-stone-50">
                <div className="mb-0.5 text-[11px] text-stone-400">{m.mode} · as of {m.as_of}</div>
                <p className="whitespace-pre-wrap">{m.text}</p>
              </div>
            </div>
          ) : <Assistant key={i} m={m} onSource={onSource} />)}
          {live && <Assistant m={live} live onSource={onSource} />}
          <div ref={end} />
        </div>
      </div>
      <div className="border-t border-stone-200 bg-white">
        <div className="mx-auto max-w-3xl px-4 py-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <div className="flex rounded-lg bg-stone-100 p-0.5">
              {MODES.map((m) => (
                <button key={m.id} onClick={() => setMode(m.id)} title={m.hint}
                  className={`rounded-md px-3 py-1 text-sm ${mode === m.id ? `${MODE_STYLE[m.id]} text-white shadow` : 'text-stone-600 hover:text-stone-900'}`}>
                  {m.label}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-1 text-sm text-stone-600">
              as of
              <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)}
                className="rounded border border-stone-300 px-2 py-0.5 text-sm" />
            </label>
            <span className="hidden text-xs text-stone-400 sm:inline">{hint}</span>
          </div>
          <div className="flex gap-2">
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
              placeholder={busy ? 'Running…' : `${MODES.find((m) => m.id === mode)!.label}: type a question (Enter to send)`}
              className="flex-1 resize-none rounded-lg border border-stone-300 px-3 py-2 focus:border-stone-500 focus:outline-none" />
            <button onClick={send} disabled={busy || !text.trim()}
              className="rounded-lg bg-stone-900 px-4 text-white disabled:opacity-40">Send</button>
          </div>
        </div>
      </div>
    </div>
  )
}
