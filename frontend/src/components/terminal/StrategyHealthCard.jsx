import { useEffect, useState } from "react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../../lib/api";

const COLOR = { OPEN: "text-neon-green border-neon-green", WARNING: "text-yellow-400 border-yellow-400", RESTRICTED: "text-orange-400 border-orange-400", DISABLED: "text-neon-red border-neon-red" };
const pf = (s) => (s?.pf_inf ? "∞" : !s?.trades ? "—" : s.profit_factor.toFixed(2));

export default function StrategyHealthCard() {
  const [gate, setGate] = useState(null);
  const [stats, setStats] = useState(null);
  const [symbols, setSymbols] = useState({});
  const [busy, setBusy] = useState("");
  const load = () => Promise.all([api.get("/strategy/gate"), api.get("/strategy/stats"), api.get("/strategy/symbols")])
    .then(([g, s, y]) => { setGate(g.data); setStats(s.data); setSymbols(y.data); }).catch(() => {});
  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);
  const run = async (key, fn, ok) => {
    setBusy(key);
    try { await fn(); toast.success(ok); await load(); } catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); } finally { setBusy(""); }
  };
  const toggleSymbol = (sym, enabled) => run(sym, () => api.post(`/strategy/symbols/${sym}`, { enabled }), `${sym} ${enabled ? "re-enabled (stats restart from now)" : "disabled"}`);
  const resetGate = () => run("gate", () => api.post("/strategy/gate/reset"), "Strategy re-enabled · rolling stats restart from now");
  const exportCsv = () => run("csv", async () => {
    const r = await api.get("/trades/audit.csv", { responseType: "blob" });
    const url = URL.createObjectURL(r.data);
    const a = Object.assign(document.createElement("a"), { href: url, download: "luchii_trade_audit.csv" });
    a.click();
    URL.revokeObjectURL(url);
  }, "Trade audit CSV downloaded");
  const o = stats?.overall;
  return (
    <div className="panel p-3" data-testid="strategy-health-card">
      <div className="flex justify-between items-center mb-2">
        <span className="panel-label">STRATEGY HEALTH · 30D</span>
        <div className="flex gap-1 items-center">
          <button data-testid="export-audit-csv" onClick={exportCsv} disabled={busy === "csv"}
            className="text-[9px] tracking-[0.15em] border border-term-border text-term-muted hover:text-white px-2 py-0.5">CSV</button>
          {gate && <span data-testid="gate-state" className={`text-[10px] tracking-[0.15em] border px-2 py-0.5 ${COLOR[gate.state]}`}>GATE {gate.state}</span>}
        </div>
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
          {gate?.state === "DISABLED" && (
            <button data-testid="gate-reset-button" onClick={resetGate} disabled={busy === "gate"}
              className="mt-1 text-[9px] tracking-[0.15em] border border-neon-green text-neon-green hover:bg-neon-green/10 px-2 py-0.5">
              OWNER RE-ENABLE STRATEGY
            </button>
          )}
          <div className="mt-2 space-y-1">
            {Object.entries(stats.by_asset).map(([a, s]) => {
              const y = symbols[a];
              const off = y?.disabled;
              return (
                <div key={a} data-testid={`stats-${a}`} className="flex justify-between items-center text-[9px] border-t border-term-border pt-1">
                  <span className={`w-8 ${off ? "text-neon-red line-through" : "text-white"}`}>{a}</span>
                  <span className="text-term-muted">PF <b className="text-white">{pf(s)}</b></span>
                  <span className="text-term-muted">WIN <b className="text-white">{s.win_rate}%</b></span>
                  <span className="text-term-muted">AVG+ <b className="text-white">{s.avg_gain ? `$${s.avg_gain}` : "—"}</b></span>
                  <span className="text-term-muted">N <b className="text-white">{s.trades}</b></span>
                  {y && (
                    <button data-testid={`symbol-toggle-${a}`} onClick={() => toggleSymbol(a, !!off)} disabled={busy === a} title={y.reason || ""}
                      className={`text-[8px] tracking-[0.1em] border px-1.5 py-0.5 ${off ? "border-neon-red text-neon-red" : "border-term-border text-term-muted hover:text-white"}`}>
                      {off ? "OFF · ENABLE" : "ON"}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
          {gate?.history?.length > 0 && (
            <div className="mt-2 text-[9px] text-term-muted" data-testid="gate-timeline">
              {gate.history.slice(-3).map((h, i) => <div key={i}>{new Date(h.ts).toLocaleDateString()} {h.old} → {h.new}{h.by === "owner" ? " · owner" : ""}</div>)}
            </div>
          )}
        </>
      )}
    </div>
  );
}
