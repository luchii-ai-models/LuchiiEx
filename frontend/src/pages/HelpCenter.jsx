import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Send, Sparkles, Bot, User, Sliders, Server, Bell, Shield, AlertTriangle } from "lucide-react";
import api, { formatApiErrorDetail } from "../lib/api";
import Logo from "../components/terminal/Logo";
import Footer from "../components/terminal/Footer";

const SUGGESTED = [
  "What's the difference between DRY and LIVE mode?",
  "How do the risk guardrails work?",
  "How do I connect MetaTrader or PocketOption?",
  "How do I set up Telegram alerts?",
  "How do I install this app on my phone?",
];

const ACTIONS = {
  settings: { label: "Open Settings", Icon: Sliders },
  brokers: { label: "Connect Brokers", Icon: Server },
  alerts: { label: "Set up Alerts", Icon: Bell },
  guardrails: { label: "Open Guardrails", Icon: Shield },
  killswitch: { label: "Go to Kill-switch", Icon: AlertTriangle },
};

function parseReply(raw) {
  const found = [];
  const re = /\[OPEN:(settings|brokers|alerts|guardrails|killswitch)\]/gi;
  let m;
  while ((m = re.exec(raw)) !== null) {
    const key = m[1].toLowerCase();
    if (!found.includes(key)) found.push(key);
  }
  const text = raw.replace(re, "").replace(/\n{2,}/g, "\n").trim();
  return { text, actions: found };
}

const GREETING = {
  role: "assistant",
  text: "Hi, I'm Luchii — Ask me anything about running the bot, guardrails, brokers or alerts. Try a suggested prompt below to get started",
  actions: [],
};

export default function HelpCenter() {
  const navigate = useNavigate();
  const sessionId = useRef(`help-${Math.random().toString(36).slice(2, 10)}`).current;
  const [messages, setMessages] = useState([GREETING]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  const send = async (text) => {
    const q = (text ?? input).trim();
    if (!q || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: q }]);
    setBusy(true);
    try {
      const { data } = await api.post("/help/chat", { message: q, session_id: sessionId });
      const { text: clean, actions } = parseReply(data.reply || "");
      setMessages((m) => [...m, { role: "assistant", text: clean, actions }]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", error: true, actions: [],
        text: formatApiErrorDetail(err.response?.data?.detail) || "Assistant is temporarily unavailable. Please try again." }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen w-full bg-black text-white flex flex-col font-mono">
      <header className="shrink-0 border-b border-term-border bg-term-surface px-3 sm:px-4 py-2 flex items-center justify-between">
        <button data-testid="help-back-button" onClick={() => navigate("/")}
          className="flex items-center gap-2 text-term-muted hover:text-neon-magenta transition-colors text-xs tracking-[0.2em] uppercase">
          <ArrowLeft size={14} /> Back to terminal
        </button>
        <span className="panel-label">◈ HELP CENTER</span>
      </header>

      <main className="flex-1 min-h-0 flex flex-col items-center px-4 py-6">
        <div className="w-full max-w-2xl flex-1 min-h-0 flex flex-col">
          <div className="flex items-center gap-3 mb-4">
            <Logo size={40} />
            <div>
              <div className="text-sm font-extrabold tracking-tighter">LUCHII</div>
            </div>
          </div>

          <div className="panel panel-glow flex-1 min-h-0 flex flex-col">
            <div ref={scrollRef} data-testid="help-messages" className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4">
              {messages.map((m, i) => (
                <div key={i} className={`flex flex-col ${m.role === "user" ? "items-end" : "items-start"}`}>
                  <div data-testid={`help-message-${m.role}`}
                    className={`flex gap-2.5 ${m.role === "user" ? "flex-row-reverse" : ""}`}>
                    <div className={`h-7 w-7 shrink-0 rounded-full border flex items-center justify-center ${m.role === "user" ? "border-neon-cyan text-neon-cyan" : "border-neon-magenta text-neon-magenta"}`}>
                      {m.role === "user" ? <User size={13} /> : <Bot size={13} />}
                    </div>
                    <div className={`max-w-[80%] px-3 py-2 text-sm leading-relaxed whitespace-pre-line border ${
                      m.role === "user"
                        ? "border-neon-cyan/40 bg-neon-cyan/5"
                        : m.error ? "border-neon-red/40 bg-neon-red/5 text-neon-red" : "border-term-border bg-term-surface"}`}>
                      {m.text}
                    </div>
                  </div>
                  {m.actions && m.actions.length > 0 && (
                    <div className="flex flex-wrap gap-2 mt-2 ml-9" data-testid={`help-actions-${i}`}>
                      {m.actions.map((a) => {
                        const cfg = ACTIONS[a];
                        if (!cfg) return null;
                        const Icon = cfg.Icon;
                        return (
                          <button key={a} data-testid={`help-action-${a}`} onClick={() => navigate(`/?panel=${a}`)}
                            className="flex items-center gap-1.5 text-[11px] border border-neon-cyan/50 text-neon-cyan hover:bg-neon-cyan hover:text-black transition-colors px-2.5 py-1.5">
                            <Icon size={12} /> {cfg.label}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              ))}
              {busy && (
                <div className="flex gap-2.5" data-testid="help-typing">
                  <div className="h-7 w-7 shrink-0 rounded-full border border-neon-magenta text-neon-magenta flex items-center justify-center"><Bot size={13} /></div>
                  <div className="px-3 py-2 border border-term-border bg-term-surface text-neon-magenta text-sm tracking-widest">
                    <span className="animate-blink">▊</span> thinking…
                  </div>
                </div>
              )}
            </div>

            {messages.length <= 1 && (
              <div className="px-4 pb-2 flex flex-wrap gap-2">
                {SUGGESTED.map((s, i) => (
                  <button key={i} data-testid={`help-prompt-${i}`} onClick={() => send(s)} disabled={busy}
                    className="text-[11px] border border-neon-magenta/40 text-neon-magenta hover:bg-neon-magenta hover:text-black transition-colors px-2.5 py-1.5 disabled:opacity-40">
                    {s}
                  </button>
                ))}
              </div>
            )}

            <form onSubmit={(e) => { e.preventDefault(); send(); }}
              className="border-t border-term-border p-2 flex items-center gap-2">
              <input data-testid="help-input" value={input} onChange={(e) => setInput(e.target.value)}
                placeholder="Ask Luchii…" disabled={busy}
                className="flex-1 bg-transparent outline-none px-2 py-2 text-sm text-white placeholder:text-term-muted/50" />
              <button data-testid="help-send-button" type="submit" disabled={busy || !input.trim()}
                className="border border-neon-magenta text-neon-magenta hover:bg-neon-magenta hover:text-black transition-colors px-3 py-2 disabled:opacity-40">
                <Send size={15} />
              </button>
            </form>
          </div>
        </div>
      </main>

      <Footer />
    </div>
  );
}
