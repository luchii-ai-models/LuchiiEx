import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Send, Plus, Trash2, Bot, User, Menu, Pin, Pencil } from "lucide-react";
import api, { formatApiErrorDetail } from "../lib/api";
import Logo from "../components/terminal/Logo";

export default function LuchiiChat() {
  const navigate = useNavigate();
  const [models, setModels] = useState([{ id: "luchii-4.7", name: "Luchii 4.7" }]);
  const [model, setModel] = useState("luchii-4.7");
  const [conversations, setConversations] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sidebar, setSidebar] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editTitle, setEditTitle] = useState("");
  const scrollRef = useRef(null);

  const loadConversations = async () => {
    try { const { data } = await api.get("/chat/conversations"); setConversations(data); } catch (e) {}
  };

  useEffect(() => {
    api.get("/chat/models").then(({ data }) => setModels(data)).catch(() => {});
    loadConversations();
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  const openConversation = async (id) => {
    setActiveId(id); setSidebar(false);
    try {
      const { data } = await api.get(`/chat/conversations/${id}`);
      setMessages(data.messages || []); setModel(data.model || "luchii-4.7");
    } catch (e) { setMessages([]); }
  };

  const newChat = () => { setActiveId(null); setMessages([]); setSidebar(false); };

  const removeConversation = async (id, e) => {
    e.stopPropagation();
    await api.delete(`/chat/conversations/${id}`);
    if (id === activeId) newChat();
    loadConversations();
  };

  const togglePin = async (c, e) => {
    e.stopPropagation();
    await api.patch(`/chat/conversations/${c.id}`, { pinned: !c.pinned });
    loadConversations();
  };

  const startRename = (c, e) => {
    e.stopPropagation();
    setEditingId(c.id);
    setEditTitle(c.title || "");
  };

  const saveRename = async (id) => {
    const t = editTitle.trim();
    setEditingId(null);
    if (t) { await api.patch(`/chat/conversations/${id}`, { title: t }); loadConversations(); }
  };

  const send = async () => {
    const q = input.trim();
    if (!q || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: q }]);
    setBusy(true);
    try {
      const { data } = await api.post("/chat/message", { conversation_id: activeId, model, message: q });
      setActiveId(data.conversation_id);
      setMessages((m) => [...m, { role: "assistant", content: data.reply }]);
      loadConversations();
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", error: true,
        content: formatApiErrorDetail(err.response?.data?.detail) || "Luchii is temporarily unavailable." }]);
    } finally { setBusy(false); }
  };

  return (
    <div className="h-screen w-full bg-black text-white flex flex-col font-mono overflow-hidden">
      <header className="shrink-0 border-b border-term-border bg-term-surface px-3 sm:px-4 py-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <button data-testid="chat-sidebar-toggle" onClick={() => setSidebar((s) => !s)}
            className="lg:hidden text-term-muted hover:text-neon-magenta"><Menu size={16} /></button>
          <button data-testid="chat-back-button" onClick={() => navigate("/")}
            className="flex items-center gap-2 text-term-muted hover:text-neon-magenta transition-colors text-xs tracking-[0.2em] uppercase">
            <ArrowLeft size={14} /> Terminal
          </button>
        </div>
        <div className="flex items-center gap-2">
          <Logo size={26} />
          <span className="text-sm font-extrabold tracking-tighter">LUCHII</span>
        </div>
        <select data-testid="chat-model-select" value={model} onChange={(e) => setModel(e.target.value)}
          className="bg-term-surface border border-term-border text-neon-magenta text-[11px] tracking-[0.1em] px-2 py-1.5 outline-none focus:border-neon-magenta">
          {models.map((m) => <option key={m.id} value={m.id} className="bg-term-surface text-white">{m.name}</option>)}
        </select>
      </header>

      <div className="flex-1 min-h-0 flex">
        {/* Sidebar */}
        <aside data-testid="chat-sidebar"
          className={`${sidebar ? "block" : "hidden"} lg:block w-64 shrink-0 border-r border-term-border bg-term-surface flex-col`}>
          <div className="p-2">
            <button data-testid="chat-new-button" onClick={newChat}
              className="w-full flex items-center justify-center gap-2 border border-neon-magenta text-neon-magenta hover:bg-neon-magenta hover:text-black transition-colors py-2 text-xs tracking-[0.15em]">
              <Plus size={13} /> NEW CHAT
            </button>
          </div>
          <div className="overflow-y-auto px-2 pb-2 space-y-1" style={{ maxHeight: "calc(100vh - 120px)" }}>
            {conversations.length === 0 && <div className="text-term-muted text-[11px] px-1 py-2">No conversations yet.</div>}
            {conversations.map((c) => (
              <div key={c.id} data-testid={`chat-conv-${c.id}`}
                onClick={() => editingId === c.id ? null : openConversation(c.id)}
                className={`group flex items-center gap-1.5 px-2 py-2 cursor-pointer border ${activeId === c.id ? "border-neon-magenta/60 bg-neon-magenta/5" : "border-transparent hover:border-term-border"}`}>
                <button data-testid={`chat-pin-${c.id}`} onClick={(e) => togglePin(c, e)}
                  className={`shrink-0 ${c.pinned ? "text-neon-magenta" : "opacity-0 group-hover:opacity-100 text-term-muted"} hover:text-neon-magenta`}>
                  <Pin size={11} fill={c.pinned ? "currentColor" : "none"} /></button>
                {editingId === c.id ? (
                  <input data-testid={`chat-rename-input-${c.id}`} autoFocus value={editTitle}
                    onClick={(e) => e.stopPropagation()} onChange={(e) => setEditTitle(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") saveRename(c.id); if (e.key === "Escape") setEditingId(null); }}
                    onBlur={() => saveRename(c.id)}
                    className="text-[11px] flex-1 min-w-0 bg-transparent border-b border-neon-magenta outline-none text-white" />
                ) : (
                  <span className="text-[11px] truncate flex-1">{c.title || "New chat"}</span>
                )}
                <button data-testid={`chat-rename-${c.id}`} onClick={(e) => startRename(c, e)}
                  className="shrink-0 opacity-0 group-hover:opacity-100 text-term-muted hover:text-neon-cyan"><Pencil size={11} /></button>
                <button data-testid={`chat-delete-${c.id}`} onClick={(e) => removeConversation(c.id, e)}
                  className="shrink-0 opacity-0 group-hover:opacity-100 text-term-muted hover:text-neon-red"><Trash2 size={12} /></button>
              </div>
            ))}
          </div>
        </aside>

        {/* Main */}
        <div className="flex-1 min-h-0 flex flex-col">
          <div ref={scrollRef} data-testid="chat-messages" className="flex-1 min-h-0 overflow-y-auto p-4 sm:p-6">
            <div className="max-w-2xl mx-auto space-y-5">
              {messages.length === 0 && (
                <div className="flex flex-col items-center justify-center text-center py-16" data-testid="chat-empty">
                  <Logo size={72} className="animate-spin-slow" />
                  <div className="mt-5 text-lg font-extrabold tracking-tighter">Ask Luchii</div>
                  <div className="mt-2 text-sm text-term-muted max-w-sm">Frasberg AI's assistant. Pick a model above and start a conversation — your chats are saved to your account.</div>
                </div>
              )}
              {messages.map((m, i) => (
                <div key={i} data-testid={`chat-message-${m.role}`} className={`flex gap-3 ${m.role === "user" ? "flex-row-reverse" : ""}`}>
                  <div className={`h-8 w-8 shrink-0 rounded-full border flex items-center justify-center ${m.role === "user" ? "border-neon-cyan text-neon-cyan" : "border-neon-magenta text-neon-magenta"}`}>
                    {m.role === "user" ? <User size={14} /> : <Bot size={14} />}
                  </div>
                  <div className={`max-w-[85%] px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-line border ${
                    m.role === "user" ? "border-neon-cyan/40 bg-neon-cyan/5"
                    : m.error ? "border-neon-red/40 bg-neon-red/5 text-neon-red" : "border-term-border bg-term-surface"}`}>
                    {m.content}
                  </div>
                </div>
              ))}
              {busy && (
                <div className="flex gap-3" data-testid="chat-typing">
                  <div className="h-8 w-8 shrink-0 rounded-full border border-neon-magenta text-neon-magenta flex items-center justify-center"><Bot size={14} /></div>
                  <div className="px-3.5 py-2.5 border border-term-border bg-term-surface text-neon-magenta text-sm tracking-widest"><span className="animate-blink">▊</span> thinking…</div>
                </div>
              )}
            </div>
          </div>

          <form onSubmit={(e) => { e.preventDefault(); send(); }}
            className="shrink-0 border-t border-term-border p-3">
            <div className="max-w-2xl mx-auto flex items-center gap-2 border border-term-border focus-within:border-neon-magenta bg-term-surface px-2">
              <input data-testid="chat-input" value={input} onChange={(e) => setInput(e.target.value)}
                placeholder="Message Luchii…" disabled={busy}
                className="flex-1 bg-transparent outline-none px-2 py-3 text-sm text-white placeholder:text-term-muted/50" />
              <button data-testid="chat-send-button" type="submit" disabled={busy || !input.trim()}
                className="border border-neon-magenta text-neon-magenta hover:bg-neon-magenta hover:text-black transition-colors px-3 py-2 disabled:opacity-40">
                <Send size={15} />
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
