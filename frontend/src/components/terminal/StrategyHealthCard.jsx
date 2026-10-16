import { useEffect, useState } from "react";
import api from "../../lib/api";

const COLOR = { OPEN: "text-neon-green border-neon-green", WARNING: "text-yellow-400 border-yellow-400", RESTRICTED: "text-orange-400 border-orange-400", DISABLED: "text-neon-red border-neon-red" };
const pf = (s) => (s?.pf_inf ? "∞" : s?.profit_factor == null ? "—" : s.profit_factor.toFixed(2));

export default function StrategyHealthCard() {
  const [gate, setGate] = useState(null);
  const [stats, setStats] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () => Promise.all([api.get("/strategy/gate"), api.get("/strategy/stats")])
      .then(([g, s]) => { if (alive) { setGate(g.data); setStats(s.data); } }).catch(() => {});
    load();
    const t = setInterval(load, 30000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  const o = stats?.overall;
  return (
    <div className="panel p-3" data-testid="strategy-health-card">
      <div className="flex justify-between items-center mb-2">
        <span className="panel-label">STRATEGY HEALTH · 30D</span>
        {gate && <span data-testid="gate-state" className={`text-[10px] tracking-[0.15em] border px-2 py-0.5 ${COLOR[gate.state]}`}>GATE {gate.state}</span>}
      </div>
      {!stats ? <p className="text-[10px] text-term-muted">Loading…</p> : (
        <>
          <div className="grid grid-cols-4 gap-1 text-[9px] text-term-muted">
            <span>PF <b data-testid="stat-pf" className="text-white">{pf(o)}</b></span>
            <span>WIN <b data-testid="stat-winrate" className="text-white">{o.win_rate}%</b></span>
            <span>AVG+ <b className="text-neon-green">{o.avg_gain > 0 ? `$${o.avg_gain}` : "—"}</b></span>
            <span>DD <b data-testid="stat-dd" className="text-white">{stats.max_drawdown}%</b></span>
          </div>
          <p className="text-[9px] text-term-muted mt-1" data-testid="gate-reason">{gate?.reason}</p>
          <div className="mt-2 space-y-1">
            {Object.entries(stats.by_asset).map(([a, s]) => (
              <div key={a} data-testid={`stats-${a}`} className="flex justify-between text-[9px] border-t border-term-border pt-1">
                <span className="text-white w-8">{a}</span>
                <span className="text-term-muted">PF <b className="text-white">{pf(s)}</b></span>
                <span className="text-term-muted">WIN <b className="text-white">{s.win_rate}%</b></span>
                <span className="text-term-muted">AVG+ <b className="text-white">{s.avg_gain ? `$${s.avg_gain}` : "—"}</b></span>
                <span className="text-term-muted">N <b className="text-white">{s.trades}</b></span>
              </div>
            ))}
          </div>
          {gate?.history?.length > 0 && (
            <div className="mt-2 text-[9px] text-term-muted" data-testid="gate-timeline">
              {gate.history.slice(-3).map((h, i) => <div key={i}>{new Date(h.ts).toLocaleDateString()} {h.old} → {h.new}</div>)}
            </div>
          )}
        </>
      )}
    </div>
  );
}
