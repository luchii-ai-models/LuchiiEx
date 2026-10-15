import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Globe, Cpu, ShieldCheck, Mail, MapPin, Send, CheckCircle2 } from "lucide-react";
import api, { formatApiErrorDetail } from "../lib/api";
import Logo from "../components/terminal/Logo";
import Footer from "../components/terminal/Footer";

const TEAM = [
  { name: "A. Frasberg", role: "Founder & CEO", tag: "Vision · Strategy" },
  { name: "M. Vale", role: "Head of AI Research", tag: "Luchii Engine" },
  { name: "R. Okafor", role: "Head of Quant Trading", tag: "Risk · Execution" },
  { name: "S. Lindqvist", role: "Lead Platform Engineer", tag: "Infra · Brokers" },
];

const MILESTONES = [
  { year: "2023", title: "Frasberg, Inc. founded", body: "Started with a mission to make autonomous trading transparent and safe." },
  { year: "2024", title: "Luchii engine v1", body: "First AI model generating live BTC signals with human-readable reasoning." },
  { year: "2025", title: "Multi-broker mirroring", body: "Signals mirrored to MetaTrader 4/5 and PocketOption in real time." },
  { year: "2026", title: "Luchii 4.7 · guardrails", body: "Kill-switch, loss limits and manual confirmation ship to every operator." },
];

export default function Company() {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen w-full bg-black text-white flex flex-col font-mono">
      <header className="shrink-0 border-b border-term-border bg-term-surface px-3 sm:px-4 py-2 flex items-center justify-between">
        <button data-testid="company-back-button" onClick={() => navigate("/")}
          className="flex items-center gap-2 text-term-muted hover:text-neon-magenta transition-colors text-xs tracking-[0.2em] uppercase">
          <ArrowLeft size={14} /> Back to terminal
        </button>
        <span className="panel-label">◈ COMPANY</span>
      </header>

      <main className="flex-1 overflow-y-auto px-6 py-10">
        <div className="w-full max-w-3xl mx-auto">
          <div className="flex flex-col items-center text-center mb-10">
            <Logo size={128} />
            <h1 className="mt-5 text-3xl sm:text-4xl font-extrabold tracking-tighter">
              FRASBERG, <span className="text-neon-magenta text-glow-magenta">INC.</span>
            </h1>
            <p className="mt-3 text-sm sm:text-base text-term-muted max-w-xl leading-relaxed">
              Frasberg, Inc. — an American multinational technology company and a subsidiary of Frasberg Financial Group Limited.
            </p>
            <p data-testid="company-parent" className="mt-2 panel-label text-[9px]">A SUBSIDIARY OF FRASBERG FINANCIAL GROUP LIMITED</p>
          </div>

          <div className="panel panel-glow p-6 mb-6" data-testid="company-mission">
            <div className="panel-label mb-2">◈ MISSION</div>
            <p className="text-sm text-white/80 leading-relaxed">
              We build autonomous, AI-driven trading infrastructure. The Luchii engine analyses live
              markets and executes with disciplined, risk-guarded precision — mirroring signals across
              exchanges and brokers in real time.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-12">
            <div className="panel p-4" data-testid="company-pillar-ai">
              <Cpu size={18} className="text-neon-cyan mb-2" />
              <div className="text-sm font-bold mb-1">Luchii AI Engine</div>
              <div className="text-xs text-term-muted leading-relaxed">Autonomous BTC decisioning with transparent reasoning.</div>
            </div>
            <div className="panel p-4" data-testid="company-pillar-risk">
              <ShieldCheck size={18} className="text-neon-green mb-2" />
              <div className="text-sm font-bold mb-1">Risk Guardrails</div>
              <div className="text-xs text-term-muted leading-relaxed">Kill-switch, loss limits and manual confirmation.</div>
            </div>
            <div className="panel p-4" data-testid="company-pillar-global">
              <Globe size={18} className="text-neon-magenta mb-2" />
              <div className="text-sm font-bold mb-1">Multi-Broker</div>
              <div className="text-xs text-term-muted leading-relaxed">Mirror trades to MT4/5 and PocketOption instantly.</div>
            </div>
          </div>

          <section className="mb-12" data-testid="company-team">
            <div className="panel-label mb-4">◈ THE TEAM</div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {TEAM.map((m) => (
                <div key={m.name} className="panel p-4 flex items-center gap-4">
                  <div className="h-11 w-11 rounded-full border border-neon-cyan/60 flex items-center justify-center text-neon-cyan text-sm font-extrabold shrink-0">
                    {m.name.split(/[\s.]+/).filter(Boolean).map((p) => p[0]).slice(0, 2).join("")}
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-bold truncate">{m.name}</div>
                    <div className="text-xs text-neon-magenta">{m.role}</div>
                    <div className="text-[10px] text-term-muted mt-0.5">{m.tag}</div>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="mb-12" data-testid="company-milestones">
            <div className="panel-label mb-4">◈ MILESTONES</div>
            <div className="relative pl-6 border-l border-term-border space-y-6">
              {MILESTONES.map((ms) => (
                <div key={ms.year} className="relative" data-testid={`milestone-${ms.year}`}>
                  <span className="absolute -left-[27px] top-1 h-3 w-3 rounded-full bg-neon-magenta shadow-[0_0_10px_rgba(255,214,10,0.8)]" />
                  <div className="text-neon-cyan text-xs font-bold tracking-[0.2em]">{ms.year}</div>
                  <div className="text-sm font-bold mt-1">{ms.title}</div>
                  <div className="text-xs text-term-muted mt-1 leading-relaxed">{ms.body}</div>
                </div>
              ))}
            </div>
          </section>

          <section className="mb-6" data-testid="company-contact">
            <div className="panel-label mb-4">◈ CONTACT</div>
            <div className="panel panel-glow p-6">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
                <div className="flex items-center gap-3 text-sm text-term-muted">
                  <Mail size={16} className="text-neon-cyan" /> hello@frasberg.ai
                </div>
                <div className="flex items-center gap-3 text-sm text-term-muted">
                  <MapPin size={16} className="text-neon-magenta" /> San Francisco · New York
                </div>
              </div>
              <ContactForm />
            </div>
          </section>
        </div>
      </main>

      <Footer />
    </div>
  );
}

function ContactForm() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setError(""); setBusy(true);
    try {
      await api.post("/contact", { name, email, message });
      setSent(true);
    } catch (err) {
      setError(formatApiErrorDetail(err.response?.data?.detail) || "Could not send. Please try again.");
    } finally { setBusy(false); }
  };

  if (sent) {
    return (
      <div data-testid="contact-success" className="flex flex-col items-center text-center py-6">
        <CheckCircle2 size={34} className="text-neon-green mb-3" />
        <div className="text-sm font-bold">Message received</div>
        <div className="text-xs text-term-muted mt-1">Thanks {name || "there"} — the Frasberg team will get back to you soon.</div>
      </div>
    );
  }

  const field = "w-full bg-transparent border-b border-neon-magenta/60 focus:border-neon-magenta outline-none py-2 text-sm";
  return (
    <form onSubmit={submit} className="space-y-4" data-testid="contact-form">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <label className="block"><span className="panel-label">NAME</span>
          <input data-testid="contact-name" value={name} onChange={(e) => setName(e.target.value)} required className={field + " mt-1"} /></label>
        <label className="block"><span className="panel-label">EMAIL</span>
          <input data-testid="contact-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required className={field + " mt-1"} /></label>
      </div>
      <label className="block"><span className="panel-label">MESSAGE</span>
        <textarea data-testid="contact-message" value={message} onChange={(e) => setMessage(e.target.value)} required rows={4}
          className={field + " mt-1 resize-none"} /></label>
      {error && <div data-testid="contact-error" className="text-neon-red text-xs">{error}</div>}
      <button data-testid="contact-submit-button" type="submit" disabled={busy}
        className="inline-flex items-center gap-2 border border-neon-magenta text-neon-magenta hover:bg-neon-magenta hover:text-black transition-colors px-5 py-2.5 text-xs tracking-[0.2em] uppercase disabled:opacity-40">
        <Send size={14} /> {busy ? "Sending…" : "Send message"}
      </button>
    </form>
  );
}
