import { useEffect, useState } from 'react'
import { getJSON } from './api'
import type { ChatSummary } from './api'
import { ChatView } from './components/ChatView'
import { ReportsView } from './components/ReportsView'
import { SourceDrawer } from './components/SourceDrawer'

type Tab = 'chat' | 'reports'

export default function App() {
  const [tab, setTab] = useState<Tab>('chat')
  const [chatId, setChatId] = useState<string | null>(null)
  const [chats, setChats] = useState<ChatSummary[]>([])
  const [source, setSource] = useState<string | null>(null)
  const [companies, setCompanies] = useState<{ ticker: string; name: string }[]>([])
  const [newKey, setNewKey] = useState(0)

  const loadChats = () => getJSON<ChatSummary[]>('/api/chats').then(setChats).catch(() => {})
  useEffect(() => {
    loadChats()
    getJSON<{ ticker: string; name: string }[]>('/api/companies').then(setCompanies).catch(() => {})
  }, [])

  async function remove(id: string) {
    await fetch(`/api/chats/${id}`, { method: 'DELETE' })
    if (id === chatId) setChatId(null)
    loadChats()
  }

  const nav = (t: Tab, label: string) => (
    <button onClick={() => setTab(t)} className={`w-full rounded-md px-3 py-1.5 text-left text-sm ${tab === t ? 'bg-stone-700 text-white' : 'text-stone-300 hover:bg-stone-800'}`}>{label}</button>
  )

  return (
    <div className="flex h-screen">
      <nav className="flex w-60 shrink-0 flex-col bg-stone-900 p-3 text-stone-100">
        <div className="mb-4 px-2 text-lg font-semibold tracking-tight">tenk</div>
        <div className="space-y-1">{nav('chat', 'Chat')}{nav('reports', 'Reports')}</div>
        {tab === 'chat' && (
          <>
            <button onClick={() => { setChatId(null); setNewKey((k) => k + 1) }}
              className="mt-5 rounded-md border border-stone-700 px-3 py-1.5 text-left text-sm hover:bg-stone-800">+ New chat</button>
            <div className="mt-4 mb-1 px-2 text-[11px] font-semibold text-stone-500 uppercase">History</div>
            <ul className="-mx-1 flex-1 space-y-0.5 overflow-auto px-1">
              {chats.map((c) => (
                <li key={c.id} className={`group flex items-center rounded-md ${c.id === chatId ? 'bg-stone-800' : 'hover:bg-stone-800/60'}`}>
                  <button onClick={() => setChatId(c.id)} className="min-w-0 flex-1 truncate px-2 py-1.5 text-left text-sm text-stone-300">{c.title}</button>
                  <button onClick={() => remove(c.id)} className="hidden px-2 text-stone-500 group-hover:block hover:text-red-400">✕</button>
                </li>
              ))}
            </ul>
          </>
        )}
      </nav>
      <main className="min-w-0 flex-1">
        {tab === 'chat'
          ? <ChatView key={newKey} chatId={chatId} companies={companies} onSource={setSource}
              onChat={(id) => { setChatId(id); loadChats() }} />
          : <div className="h-full overflow-auto"><ReportsView companies={companies} /></div>}
      </main>
      <SourceDrawer id={source} onClose={() => setSource(null)} />
    </div>
  )
}
