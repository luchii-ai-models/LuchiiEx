import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Maximize2, X } from "lucide-react";
import api from "../../lib/api";

const color = (a) => (a === "BUY" ? "text-neon-green" : a === "SELL" ? "text-neon-red" : "text-term-muted");
const fmt = (ts) => new Date(ts).toLocaleTimeString("en-GB");

function Row({ l, i, full }) {
  return (
    <div className={`flex gap-2 min-w-0 ${i === 0 ? "animate-flash-in" : ""}`}>
      <span className="text-term-muted shrink-0">{full ? new Date(l.ts).toLocaleString("en-GB") : fmt(l.ts)}</span>
      <span className={`shrink-0 font-bold ${color(l.action)}`}>[{l.action}]</span>
      <span className="text-neon-cyan shrink-0 tnum">${l.price.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
      <span className={`shrink-0 tnum ${l.pct >= 0 ? "text-neon-green" : "text-neon-red"}`}>{l.pct >= 0 ? "+" : ""}{l.pct}%</span>
      <span className="text-term-muted/70 shrink-0 hidden sm:inline">[{l.engine}·{Math.round(l.confidence * 100)}%]</span>
      <span className={`text-white/80 min-w-0 flex-1 ${full ? "whitespace-pre-wrap break-words" : "truncate"}`}>{l.reason}</span>
      {l.fill && <span className="text-neon-amber shrink-0 hidden sm:inline">« {l.fill}</span>}
    </div>
  );
}

function FullLog({ onClose }) {
  const [rows, setRows] = useState(null);
  const [filter, setFilter] = useState("ALL");
  useEffect(() => {
    const load = () => api.get("/bot/logs/day").then((r) => setRows(r.data)).catch(() => setRows([]));
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);
  const shown = (rows || []).filter((l) => filter === "ALL" || l.action === filter || (filter === "FILLS" && l.fill));
  return (
    <div className="fixed inset-0 z-[200] bg-black/95 flex flex-col font-mono" data-testid="full-log-modal">
      <div className="px-3 py-2 border-b border-term-border flex items-center justify-between gap-2 flex-wrap">
        <span className="panel-label">◈ LOG · LAST 24H · {rows ? rows.length : "…"} ENTRIES</span>
        <div className="flex items-center gap-1">
          {["ALL", "BUY", "SELL", "FILLS", "HOLD"].map((f) => (
            <button key={f} data-testid={`full-log-filter-${f}`} onClick={() => setFilter(f)}
              className={`text-[9px] tracking-[0.15em] px-2 py-0.5 border ${filter === f ? "border-neon-magenta text-neon-magenta" : "border-term-border text-term-muted hover:text-white"}`}>{f}</button>
          ))}
          <button data-testid="full-log-close" onClick={onClose} className="ml-2 text-term-muted hover:text-neon-red"><X size={16} /></button>
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto px-3 py-2 text-[11px] leading-relaxed space-y-0.5">
        {rows === null && <div className="text-term-muted">loading…</div>}
        {rows && shown.length === 0 && <div className="text-term-muted">no entries</div>}
        {shown.map((l, i) => <Row key={l.ts + "-" + i} l={l} i={i} full />)}
      </div>
    </div>
  );
}

export default function ExecutionLog({ logs }) {
  const rows = logs || [];
  const [open, setOpen] = useState(false);
  return (
    <div className="panel h-40 shrink-0 flex flex-col">
      <div className="px-2 py-1.5 border-b border-term-border flex items-center justify-between">
        <span className="panel-label">◈ LOG<span className="animate-blink text-neon-magenta">▊</span></span>
        <div className="flex items-center gap-2">
          <button data-testid="open-full-log" onClick={() => setOpen(true)} title="Full-screen 24h log"
            className="text-term-muted hover:text-neon-magenta transition-colors"><Maximize2 size={12} /></button>
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-2 py-1 text-[10px] leading-relaxed" data-testid="execution-log">
        {rows.length === 0 && (
          <div className="text-term-muted">$ engine idle — press START to begin autonomous trading<span className="animate-blink">_</span></div>
        )}
        {rows.map((l, i) => <Row key={l.ts + "-" + i} l={l} i={i} />)}
      </div>
      {open && createPortal(<FullLog onClose={() => setOpen(false)} />, document.body)}
    </div>
  );
}
