/* Presentation for fast reading: numbers stand out, citations become chips, notes fold by section. */
import { Children, cloneElement, isValidElement, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Answer, Claim } from '../api'

// Tickers in parentheses or standing alone, e.g. "(BKNG)", "EXPE's": keywords worth bolding
const TICKER = /\b([A-Z]{2,5})\b(?=\)|'s|\b)/g

// $1.3B · +16.5% · 9.4x · +0.1 pts · 148.3M · $85.26 · -$368M (bare integers and years stay plain)
const NUM = /([+\-−]?\$?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|x|pts|bps|[BMKT]|bn|billion|million|trillion))?)(?![\w%])/g

function isFigure(s: string) {
  return /[$%xBMKT]|pts|bps|bn|illion|\./.test(s) || /^[+\-−]/.test(s)
}

function tone(s: string) {
  if (!/%|pts|bps/.test(s)) return 'text-neutral-900'
  if (/^\+/.test(s)) return 'text-emerald-700'
  if (/^[-−]/.test(s)) return 'text-red-700'
  return 'text-neutral-900'
}

/** Text with its figures in semibold; signed percentages green or red. */
export function highlight(text: string, key = ''): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(NUM)) {
    const s = m[1], i = m.index!
    const prev = text[i - 1]
    if (!isFigure(s) || (prev && /[\w.]/.test(prev))) continue
    if (i > last) out.push(text.slice(last, i))
    out.push(<span key={`${key}${i}`} className={`font-semibold tabular-nums ${tone(s)}`}>{s}</span>)
    last = i + s.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out.flatMap((part, j) => typeof part === 'string' ? tickers(part, `${key}t${j}-`) : [part])
}

let KNOWN = new Set<string>()
/** The coverage's tickers, bolded wherever they appear (App sets them from /api/companies). */
export function setTickers(list: string[]) { KNOWN = new Set(list) }

function tickers(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(TICKER)) {
    if (!KNOWN.has(m[1])) continue
    if (m.index! > last) out.push(text.slice(last, m.index))
    out.push(<b key={`${key}${m.index}`} className="font-semibold text-neutral-900">{m[1]}</b>)
    last = m.index! + m[1].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

/** Apply `highlight` to every string inside rendered markdown children. */
function deep(children: ReactNode): ReactNode {
  return Children.map(children, (c, i) => {
    if (typeof c === 'string') return highlight(c, `${i}-`)
    if (isValidElement<{ children?: ReactNode }>(c) && c.props.children) return cloneElement(c, {}, deep(c.props.children))
    return c
  })
}

// --- chat answers -------------------------------------------------------------------------------

function Cite({ n, claim, onSource }: { n: string; claim?: Claim; onSource: (id: string) => void }) {
  const weak = claim?.status === 'unverified'
  const source = claim?.chunk_ids[0]
  return (
    <button onClick={() => source && onSource(source)} disabled={!source}
      title={claim ? `${claim.status}${claim.status_reason ? ` (${claim.status_reason})` : ''}: ${claim.text}` : `claim ${n}`}
      className={`mx-px inline-flex h-4 min-w-4 -translate-y-1.5 items-center justify-center rounded-full px-1 text-[10px] leading-none ${
        weak ? 'bg-amber-100 text-amber-800 hover:bg-amber-200' : 'bg-neutral-200 text-neutral-600 hover:bg-neutral-300'}`}>{n}</button>
  )
}

/** One sentence or line: figures highlighted, [n] markers as clickable chips. */
function Cited({ text, claims, onSource }: { text: string; claims: Map<string, Claim>; onSource: (id: string) => void }) {
  const parts = text.split(/(\[\d+\])/g)
  return <>{parts.map((p, i) => {
    const m = /^\[(\d+)\]$/.exec(p)
    return m ? <Cite key={i} n={m[1]} claim={claims.get(m[1])} onSource={onSource} /> : <span key={i}>{highlight(p, `${i}-`)}</span>
  })}</>
}

function sentences(text: string): string[] {
  // not after abbreviations such as "Adj. EBITDA", "vs. peers", "Inc. The", "U.S. sales"
  return text.split(/(?<=[.!?](?:\s*\[\d+\])*)(?<!\b(?:Adj|Inc|Corp|Co|Ltd|vs|est|approx|incl|excl|e\.g|i\.e|U\.S|No|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.)\s+(?=[A-Z"“(])/)
    .map((s) => s.trim()).filter(Boolean)
}

/** The answer as a bottom line plus one bullet per point. */
export function AnswerText({ answer, onSource }: { answer: Answer; onSource: (id: string) => void }) {
  const claims = useMemo(() => new Map(answer.claims.map((c) => [String(c.id), c])), [answer.claims])
  const lines = answer.answer.split(/\n+/).map((l) => l.trim()).filter(Boolean)
  const list = lines.length > 1 ? lines.map((l) => l.replace(/^([-*•]|\d+[.)])\s+/, '')) : sentences(answer.answer)
  const [lead, ...rest] = list
  if (!lead) return null
  return (
    <div className="text-[15px] leading-relaxed text-neutral-800">
      <p className={lead.length <= 90 ? 'text-[16px] font-semibold text-neutral-950' : 'text-[16px] text-neutral-900'}>
        <Cited text={lead} claims={claims} onSource={onSource} /></p>
      {rest.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {rest.map((s, i) => (
            <li key={i} className="flex gap-2.5">
              <span className="mt-[0.6em] h-1.5 w-1.5 shrink-0 rounded-full bg-neutral-400" />
              <span><Cited text={s} claims={claims} onSource={onSource} /></span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// --- report notes -------------------------------------------------------------------------------

interface Section { title: string; body: string }

function parseNote(md: string) {
  const [head, ...parts] = md.split(/^## /m)
  const sections: Section[] = parts.map((p) => {
    const i = p.indexOf('\n')
    return { title: (i < 0 ? p : p.slice(0, i)).trim(), body: i < 0 ? '' : p.slice(i + 1) }
  })
  const titleLine = head.match(/^# (.+)$/m)?.[1] ?? ''
  const meta = head.replace(/^# .+$/m, '').trim()
  return { titleLine, meta, sections }
}

/** "2.1 Headline" lines → sub-headings; "Bull: …" labels → their own paragraphs. */
const LABEL = /^(Bull|Bear|Hypothesis|Trigger|Data gap|Rating \/ PT change|Method|Status):\s*/

function tidy(body: string) {
  const blocks = body
    .replace(/^(\d+\.\d+) (.+)$/gm, '\n### $1 $2\n')
    .replace(/^(?=(Bull|Bear|Hypothesis|Trigger|Data gap|Rating \/ PT change|Method|Status):)/gm, '\n')
    .split(/\n\s*\n/)
  return blocks.map((b) => {
    const t = b.trim()
    if (!t || /^(\||#|[-*] |\d+[.)] )/.test(t)) return t
    const label = LABEL.exec(t)
    const text = label ? t.slice(label[0].length) : t
    const parts = sentences(text.replace(/\s*\n\s*/g, ' '))
    const head = label ? `**${label[1]}:**` : ''
    if (label) return `${head} ${text.replace(/\s*\n\s*/g, ' ')}`  // keeps its coloured edge
    if (parts.length < 2) return t
    return parts.map((p) => `- ${p}`).join('\n')  // several sentences read faster as bullets
  }).join('\n\n')
}

function firstTable(body: string): Record<string, string> | null {
  const rows = body.split('\n').filter((l) => l.trim().startsWith('|'))
  if (rows.length < 3) return null
  const cells = (l: string) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
  const [h, , v] = rows.map(cells)
  return Object.fromEntries(h.map((k, i) => [k, v[i] ?? '']))
}

const LABEL_TONE: Record<string, string> = {
  Bull: 'border-emerald-400', Bear: 'border-red-400', Hypothesis: 'border-neutral-500',
  Trigger: 'border-amber-400', 'Data gap': 'border-neutral-300',
}

const md = {
  p: ({ children }: { children?: ReactNode }) => {
    const first = Children.toArray(children)[0]
    const label = isValidElement<{ children?: ReactNode }>(first) && first.type === 'strong'
      ? String(Children.toArray(first.props.children)[0] ?? '').replace(/:$/, '') : ''
    const border = LABEL_TONE[label]
    return <p className={border ? `border-l-2 pl-3 ${border}` : ''}>{deep(children)}</p>
  },
  li: ({ children }: { children?: ReactNode }) => <li>{deep(children)}</li>,
  table: ({ node }: { node?: HNode }) => <NoteTable node={node} />,
}

// --- tables: alignment decided per column, so headers and cells line up --------------------------

interface HNode { type: string; tagName?: string; value?: string; children?: HNode[] }

function textOf(n: HNode): string {
  return n.type === 'text' ? n.value ?? '' : (n.children ?? []).map(textOf).join('')
}

function rowsOf(n: HNode | undefined, tag: string): string[][] {
  const rows: string[][] = []
  const walk = (x: HNode) => {
    if (x.tagName === 'tr') rows.push((x.children ?? []).filter((c) => c.tagName === tag).map((c) => textOf(c).trim()))
    else (x.children ?? []).forEach(walk)
  }
  if (n) walk(n)
  return rows.filter((r) => r.length)
}

const FIGURE = /^[+\-−]?\$?\d[\d,.]*\s?(%|x|pts|bps|[BMKT])?(\s?[–-]\s?[+\-−]?\$?\d[\d,.]*\s?(%|x|pts|[BMKT])?)?$/
const BLANK = /^(n\/a|—|-|)$/

function NoteTable({ node }: { node?: HNode }) {
  const head = rowsOf(node, 'th')[0] ?? []
  const body = rowsOf(node, 'td')
  const width = Math.max(head.length, ...body.map((r) => r.length))
  // a column is numeric when most of its filled cells are figures; the first column is the label
  const numeric = Array.from({ length: width }, (_, j) => {
    const cells = body.map((r) => r[j] ?? '').filter((c) => !BLANK.test(c))
    return j > 0 && cells.length > 0 && cells.filter((c) => FIGURE.test(c)).length / cells.length >= 0.6
  })
  const align = (j: number) => (numeric[j] ? 'text-right tabular-nums' : 'text-left')
  return (
    <div className="note-table">
      <table>
        {head.length > 0 && <thead><tr>{head.map((h, j) => <th key={j} className={align(j)}>{h}</th>)}</tr></thead>}
        <tbody>{body.map((r, i) => (
          <tr key={i}>{Array.from({ length: width }, (_, j) => {
            const c = r[j] ?? ''
            return <td key={j} className={`${align(j)} ${numeric[j] ? (BLANK.test(c) ? 'text-neutral-400' : tone(c)) : ''}`}>{c}</td>
          })}</tr>
        ))}</tbody>
      </table>
    </div>
  )
}

function Markdown({ text }: { text: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} components={md}>{text}</ReactMarkdown>
}

const FOLDED = /EVIDENCE LOG|EXCEPTION LOG|SOURCES, ASSUMPTIONS/i

function slug(s: string) { return s.toLowerCase().replace(/[^a-z0-9]+/g, '-') }

function Tile({ label, value, tone: t }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-2xl bg-white px-4 py-3 shadow-[0_1px_6px_rgba(0,0,0,0.05)] ring-1 ring-neutral-200">
      <div className="text-[11px] font-medium tracking-wide text-neutral-500 uppercase">{label}</div>
      <div className={`mt-0.5 text-xl font-semibold tabular-nums ${t ?? 'text-neutral-900'}`}>{value}</div>
    </div>
  )
}

const RATING_TONE: Record<string, string> = { Buy: 'text-emerald-700', Sell: 'text-red-700' }

/** A research note: KPI tiles, key takeaways, a section index, and foldable sections. */
export function NoteView({ note }: { note: string }) {
  const { titleLine, sections } = useMemo(() => parseNote(note), [note])
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const summary = sections[0]
  const kpis = summary ? firstTable(summary.body) : null
  const takeaways = summary ? summary.body.split('\n').filter((l) => /^- /.test(l)).map((l) => l.slice(2)) : []
  const summaryRest = summary ? summary.body.split('\n').filter((l) => !/^- /.test(l) && !l.trim().startsWith('|')).join('\n') : ''
  const isOpen = (s: Section) => open[s.title] ?? !FOLDED.test(s.title)
  const name = (t: string) => t.replace(/^\d+\.\s*/, '').toLowerCase().replace(/^./, (c) => c.toUpperCase())

  return (
    <div>
      {/* the as-of, status and approval caveat are in the page header; keep company and note type */}
      <p className="text-sm text-neutral-600">{titleLine.replace(/\s*\([A-Z.]+\)/, '')}</p>

      {kpis && (
        <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Object.entries(kpis).map(([k, v]) => (
            <Tile key={k} label={k} value={v} tone={RATING_TONE[v] ?? (/Return/i.test(k) ? tone(v) : undefined)} />
          ))}
        </div>
      )}

      {takeaways.length > 0 && (
        <div className="mt-5 rounded-3xl bg-neutral-900 p-6 text-neutral-100">
          <div className="mb-3 text-xs font-medium tracking-wide text-neutral-400 uppercase">Key takeaways</div>
          <ul className="space-y-4">
            {takeaways.map((t, i) => {
              const m = /^([^:]{2,30}):\s*(.*)$/.exec(t)
              return (
                <li key={i} className="text-[15px] leading-relaxed">
                  {m && <div className="text-xs font-medium tracking-wide text-neutral-400 uppercase">{m[1].replace(/\s*\(.*\)/, '')}</div>}
                  <span className="[&_.text-neutral-900]:text-white [&_.text-emerald-700]:text-emerald-300 [&_.text-red-700]:text-red-300">{highlight(m ? m[2] : t)}</span>
                </li>
              )
            })}
          </ul>
          {summaryRest.trim() && <div className="mt-4 text-xs text-neutral-400">{summaryRest.trim()}</div>}
        </div>
      )}


      <div className="mt-6 space-y-3">
        {sections.slice(1).map((s) => (
          <section key={s.title} id={slug(s.title)} className="rounded-3xl border border-neutral-200 bg-white">
            <button onClick={() => setOpen((o) => ({ ...o, [s.title]: !isOpen(s) }))}
              className="flex w-full items-center gap-3 px-6 py-4 text-left">
              <span className={`text-neutral-400 transition-transform ${isOpen(s) ? 'rotate-90' : ''}`}>▸</span>
              <span className="flex-1 text-base font-semibold text-neutral-900">{name(s.title)}</span>
              {FOLDED.test(s.title) && <span className="text-xs text-neutral-400">{s.body.split('\n').filter((l) => /^\| [A-Z]-?\d/.test(l)).length || ''}</span>}
            </button>
            {isOpen(s) && <div className="prose-note px-6 pb-6"><Markdown text={tidy(s.body)} /></div>}
          </section>
        ))}
      </div>
    </div>
  )
}
