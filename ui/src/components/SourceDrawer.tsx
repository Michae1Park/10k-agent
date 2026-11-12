import { useEffect, useState } from 'react'
import { getJSON } from '../api'

export function SourceDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const [data, setData] = useState<any>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!id) return
    setData(null); setError('')
    getJSON(`/api/sources/${encodeURIComponent(id)}`).then(setData).catch((e) => setError(String(e)))
  }, [id])

  if (!id) return null
  return (
    <aside className="fixed inset-y-0 right-0 z-20 flex w-full max-w-xl flex-col border-l border-stone-200 bg-white shadow-xl">
      <header className="flex items-center justify-between border-b border-stone-200 px-4 py-3">
        <div className="min-w-0">
          <div className="truncate font-mono text-sm">{id}</div>
          {data?.kind === 'chunk' && (
            <div className="text-xs text-stone-500">
              {data.ticker} · {data.form} {data.fiscal_period === 'FY' ? `FY${data.fiscal_year}` : `${data.fiscal_year}${data.fiscal_period}`}
              {' '}· item {data.item} · {data.chunk_kind}{data.pages ? ` · p.${data.pages.join('–')}` : ''}
              {data.filed ? ` · filed ${data.filed}` : ''}{data.units ? ` · ${data.units}` : ''}
            </div>
          )}
          {data?.kind === 'fact' && <div className="text-xs text-stone-500">XBRL fact</div>}
          {data?.kind === 'data' && <div className="text-xs text-stone-500">{data.ref_kind}</div>}
        </div>
        <button onClick={onClose} className="rounded px-2 py-1 text-stone-500 hover:bg-stone-100">✕</button>
      </header>
      <div className="flex-1 overflow-auto p-4">
        {error && <p className="text-sm text-red-700">{error}</p>}
        {!data && !error && <p className="text-sm text-stone-400">Loading…</p>}
        {data?.kind === 'chunk' && <pre className="font-sans text-sm leading-relaxed whitespace-pre-wrap">{data.text}</pre>}
        {data?.kind === 'fact' && <pre className="text-xs whitespace-pre-wrap">{JSON.stringify(data.fact, null, 2)}</pre>}
        {data?.kind === 'data' && <pre className="text-xs whitespace-pre-wrap">{JSON.stringify({ values: data.values, ...data.detail }, null, 2)}</pre>}
        {data?.kind === 'other' && <p className="text-sm text-stone-600">{data.text}</p>}
      </div>
    </aside>
  )
}
