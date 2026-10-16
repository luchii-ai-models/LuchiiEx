import { useEffect, useState } from "react";
import api from "../../lib/api";

const REG = { BULL: "text-neon-green", BEAR: "text-neon-red", CHOP: "text-term-muted" };
const fmt = (v, d = 2, suf = "") => (v == null ? "—" : `${Number(v).toFixed(d)}${suf}`);

export default function SignalScoreCard({ bot }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.get("/strategy/metrics").then((r) => alive && setData(r.data)).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  const assets = data ? Object.entries(data.assets) : [];
  return (
    <div className="panel p-3" data-testid="signal-score-card">
      <div className="flex justify-between items-center mb-2">
        <span className="panel-label">SIGNAL SCORE</span>
        <span data-testid="strategy-name" className="text-[10px] text-neon-magenta tracking-[0.15em]">{data?.strategy === "aggressive" ? "AGGRESSIVE" : "LUCHII_003"}</span>
      </div>
      {!assets.length && <p className="text-[10px] text-term-muted">Loading…</p>}
      <div className="space-y-2">
        {assets.map(([a, m]) => (
          <div key={a} data-testid={`signal-${a}`} className="border border-term-border p-2">
            <div className="flex justify-between items-center text-xs">
              <span className="text-white font-semibold">{a} <span className={`text-[10px] ${REG[m.regime] || "text-term-muted"}`}>{m.regime}</span></span>
              <span data-testid={`signal-action-${a}`} className={`text-[10px] tracking-[0.15em] ${m.action === "BUY" ? "text-neon-green" : m.action === "SELL" ? "text-neon-red" : "text-term-muted"}`}>{m.action}</span>
            </div>
            <div className="mt-1 h-1.5 bg-term-border/50">
              <div data-testid={`signal-score-${a}`} className={`h-full ${m.score >= 80 ? "bg-neon-green" : m.score >= 60 ? "bg-neon-cyan" : "bg-term-muted"}`} style={{ width: `${m.score}%` }} />
            </div>
            <div className="grid grid-cols-4 gap-1 mt-1 text-[9px] text-term-muted">
              <span>SCORE <b className="text-white">{m.score}</b>/100</span>
              <span>TREND <b className="text-white">{fmt(m.trend_pct, 2, "%")}</b></span>
              <span>VOL <b className="text-white">{fmt(m.volume_ratio, 1, "x")}</b></span>
              <span>ADX <b className="text-white">{fmt(m.adx, 0)}</b></span>
              <span>RSI <b className="text-white">{fmt(m.rsi, 0)}</b></span>
              <span>EXP.MOVE <b className="text-white">{fmt(m.expected_move_pct, 2, "%")}</b></span>
              <span className="col-span-2">STOP <b className="text-white">{m.stop ? `$${Number(m.stop).toLocaleString(undefined, { maximumFractionDigits: 2 })}` : "—"}</b></span>
            </div>
            <p className="text-[9px] text-term-muted mt-1 line-clamp-2" title={m.reason}>{m.reason}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
