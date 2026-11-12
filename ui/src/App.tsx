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

  const [open, setOpen] = useState(true)
  const nav = (t: Tab, label: string, icon: string) => (
    <button onClick={() => setTab(t)} title={label}
      className={`flex w-full items-center gap-3 rounded-full px-3 py-2 text-left text-sm ${tab === t ? 'bg-neutral-300/70 font-medium text-neutral-900' : 'text-neutral-700 hover:bg-neutral-200'}`}>
      <span className="w-4 text-center">{icon}</span>{open && label}</button>
  )

  return (
    <div className="flex h-screen">
      <nav className={`flex shrink-0 flex-col bg-neutral-100 p-3 text-neutral-800 transition-[width] duration-200 ${open ? 'w-68' : 'w-16'}`}>
        <button onClick={() => setOpen(!open)} title="Menu" className="mb-4 h-10 w-10 rounded-full text-lg text-neutral-600 hover:bg-neutral-200">☰</button>
        <button onClick={() => { setTab('chat'); setChatId(null); setNewKey((k) => k + 1) }} title="New chat"
          className="mb-4 flex items-center gap-3 rounded-full bg-neutral-200/80 px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-300">
          <span className="w-4 text-center text-lg leading-none">+</span>{open && 'New chat'}</button>
        <div className="space-y-1">{nav('chat', 'Chat', '💬')}{nav('reports', 'Reports', '📄')}</div>
        {tab === 'chat' && open && (
          <>
            <div className="mt-5 mb-1 px-3 text-sm font-medium text-neutral-600">Recent</div>
            <ul className="-mx-1 flex-1 space-y-0.5 overflow-auto px-1">
              {chats.map((c) => (
                <li key={c.id} className={`group flex items-center rounded-full ${c.id === chatId ? 'bg-neutral-300/70' : 'hover:bg-neutral-200'}`}>
                  <button onClick={() => setChatId(c.id)} className="min-w-0 flex-1 truncate px-3 py-1.5 text-left text-sm text-neutral-800">{c.title}</button>
                  <button onClick={() => remove(c.id)} className="hidden px-3 text-neutral-400 group-hover:block hover:text-red-500">✕</button>
                </li>
              ))}
            </ul>
          </>
        )}
      </nav>
      <main className="bg-glow relative min-w-0 flex-1">
        <div className="absolute top-0 left-0 z-10 px-5 py-4 text-xl text-neutral-700">Equity Research Agent</div>
        {tab === 'chat'
          ? <ChatView key={newKey} chatId={chatId} companies={companies} onSource={setSource}
              onChat={(id) => { setChatId(id); loadChats() }} />
          : <div className="h-full overflow-auto pt-12"><ReportsView companies={companies} /></div>}
      </main>
      <SourceDrawer id={source} onClose={() => setSource(null)} />
    </div>
  )
}
