export default function ExecutionLog({ logs }) {
  const rows = logs || [];
  const fmt = (ts) => new Date(ts).toLocaleTimeString("en-GB");

  const color = (a) => (a === "BUY" ? "text-neon-green" : a === "SELL" ? "text-neon-red" : "text-term-muted");

  return (
    <div className="panel h-40 shrink-0 flex flex-col">
      <div className="px-2 py-1.5 border-b border-term-border flex items-center justify-between">
        <span className="panel-label">◈ EXECUTION LOG · LIVE<span className="animate-blink text-neon-magenta">▊</span></span>
        <span className="panel-label text-[8px]">LUCHII ENGINE</span>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-2 py-1 text-[10px] leading-relaxed" data-testid="execution-log">
        {rows.length === 0 && (
          <div className="text-term-muted">$ engine idle — press START to begin autonomous trading<span className="animate-blink">_</span></div>
        )}
        {rows.map((l, i) => (
          <div key={l.ts + "-" + i} className={`flex gap-2 min-w-0 ${i === 0 ? "animate-flash-in" : ""}`}>
            <span className="text-term-muted shrink-0">{fmt(l.ts)}</span>
            <span className={`shrink-0 font-bold ${color(l.action)}`}>[{l.action}]</span>
            <span className="text-neon-cyan shrink-0 tnum">${l.price.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
            <span className={`shrink-0 tnum ${l.pct >= 0 ? "text-neon-green" : "text-neon-red"}`}>{l.pct >= 0 ? "+" : ""}{l.pct}%</span>
            <span className="text-term-muted/70 shrink-0 hidden sm:inline">[{l.engine}·{Math.round(l.confidence * 100)}%]</span>
            <span className="text-white/80 truncate min-w-0 flex-1">{l.reason}</span>
            {l.fill && <span className="text-neon-amber shrink-0 hidden sm:inline">« {l.fill}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}
