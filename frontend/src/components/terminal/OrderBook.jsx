
export default function OrderBook({ snapshot }) {
  const light = false;
  const bids = (snapshot?.bids || []).slice(0, 8);
  const asks = (snapshot?.asks || []).slice(0, 8);
  const maxSize = Math.max(1, ...bids.map((b) => b.size), ...asks.map((a) => a.size));

  const Row = ({ o, side }) => {
    const pct = Math.min(100, (o.size / maxSize) * 100);
    const color = side === "bid" ? (light ? "#000000" : "#39ff14") : "#ff003c";
    return (
      <div className="relative flex justify-between px-2 py-[3px] text-[10px] tnum">
        <div className="absolute top-0 bottom-0 right-0" style={{ width: `${pct}%`, background: `${color}18` }} />
        <span className="relative z-10" style={{ color }}>{o.price.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
        <span className="relative z-10 text-term-muted">{o.size.toFixed(4)}</span>
      </div>
    );
  };

  return (
    <div className="panel p-0 shrink-0">
      <div className="px-2 py-1.5 border-b border-term-border flex items-center justify-between">
        <span className="panel-label">◈ ORDER BOOK</span>
      </div>
      <div className="flex flex-col-reverse">
        {asks.map((a, i) => <Row key={`a${i}`} o={a} side="ask" />)}
      </div>
      <div className="text-center py-1 border-y border-term-border tnum text-sm font-bold text-neon-magenta text-glow-magenta">
        {snapshot?.price ? `$${snapshot.price.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : "—"}
      </div>
      <div>
        {bids.map((b, i) => <Row key={`b${i}`} o={b} side="bid" />)}
      </div>
    </div>
  );
}
