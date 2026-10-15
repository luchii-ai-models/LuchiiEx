export default function RecentTrades({ trades }) {
  const rows = (trades || []).slice(0, 10);
  return (
    <div className="panel p-0 flex flex-col min-h-0">
      <div className="px-2 py-1.5 border-b border-term-border">
        <span className="panel-label">◈ RECENT</span>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {rows.length === 0 && <div className="panel-label text-[9px] p-2">NO TRADES YET</div>}
        {rows.map((t, i) => (
          <div key={i} className="flex items-center justify-between px-2 py-[3px] text-[10px] tnum border-b border-term-border/50">
            <span className={t.side === "BUY" ? "text-neon-green" : "text-neon-red"}>{t.side}</span>
            <span className="text-white">${t.price.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
            <span className={t.pnl >= 0 ? "text-neon-green" : "text-neon-red"}>
              {t.side === "SELL" ? `${t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(2)}` : "—"}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
