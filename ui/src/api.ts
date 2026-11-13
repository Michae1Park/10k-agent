export type Mode = 'ask' | 'research' | 'investigate'

export interface Claim {
  id: number | string
  text: string
  value?: number | string | null
  unit?: string | null
  company?: string | null
  period?: string | null
  fiscal_year?: number | null
  chunk_ids: string[]
  calculation?: unknown
  status: string
  status_reason?: string
  evidence?: string
  [k: string]: unknown
}

export interface Answer {
  answer: string
  claims: Claim[]
  table?: { columns?: string[]; rows?: unknown[][]; title?: string; [k: string]: unknown } | null
  scope_notice?: string | null
  abstained?: boolean
  assumptions?: unknown[]
  work?: string
}

export interface TraceEvent {
  type: string
  t?: number
  scope?: string | null
  [k: string]: any
}

export interface SubRun {
  id: string
  question: string
  mode: string
  why: string
  follow_up: boolean
  answer: Answer | null
  stop: string
  calls: number
  seconds: number
}

export interface AssistantMessage {
  role: 'assistant'
  mode: Mode
  answer?: Answer
  stop?: string
  seconds?: number
  started?: number  // epoch ms the run started (live only)
  error?: string
  investigation?: { sub_runs: SubRun[]; log: string[]; fallback: boolean }
  rewritten?: string | null  // the follow-up as a standalone question (history: rewrite)
  history?: { strategy: History; turns: number }
  trace?: TraceEvent[]
}

/** How a follow-up uses the chat's earlier turns; '' = the server's default (config chat.history). */
export type History = '' | 'append' | 'rewrite' | 'none'

export interface UserMessage {
  role: 'user'
  mode: Mode
  as_of: string
  text: string
}

export type Message = UserMessage | AssistantMessage

export interface ChatSummary { id: string; title: string; updated: number; messages: number; running?: boolean }

export async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}

export async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}

/** Read a text/event-stream response (GET or POST), calling onEvent per `data:` line. */
export async function stream(url: string, init: RequestInit, onEvent: (e: TraceEvent) => void,
                             signal?: AbortSignal) {
  const r = await fetch(url, { ...init, signal })
  if (!r.ok || !r.body) throw new Error(`${r.status} ${await r.text()}`)
  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let i
    while ((i = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, i)
      buffer = buffer.slice(i + 2)
      for (const line of chunk.split('\n')) {
        if (line.startsWith('data: ')) onEvent(JSON.parse(line.slice(6)))
      }
    }
  }
}

export const today = () => new Date().toISOString().slice(0, 10)

export const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: 'ask', label: 'Ask', hint: 'What was reported: retrieved and cited' },
  { id: 'research', label: 'Research', hint: 'Compute an estimate, scenario or valuation from the model' },
  { id: 'investigate', label: 'Investigate', hint: 'Plan Ask and Research sub-questions, then combine' },
]
