import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, ShieldCheck, ShieldAlert, Play, RotateCcw } from "lucide-react";
import {
  ResponsiveContainer, ComposedChart, Line, Area, BarChart, Bar, Cell,
  RadarChart, Radar, PolarGrid, PolarAngleAxis, XAxis, YAxis, Tooltip, CartesianGrid,
} from "recharts";
import api from "../lib/api";
import { toast } from "sonner";

const AX = { fontSize: 9, fill: "#6b7280" };
const TT = { background: "#050505", border: "1px solid #1f2937", fontSize: 11, color: "#fff" };
const clamp = (v, lo = 0, hi = 100) => Math.max(lo, Math.min(hi, v));

function RiskCharts({ r }) {
  const bt = r.backtest || {}, mc = r.monte_carlo || {};
  const eq = r.equity_curve || [], dd = r.drawdown_curve || [];
  const overlay = eq.map((v, i) => ({ i, equity: v, dd: dd[i] ?? null }));
  const stress = (r.stress || []).map((s) => ({ name: s.scenario.replace(/_/g, " "), loss: Number(s.loss_pct) || 0, survived: s.survived }));
  const radar = [
    { metric: "Sharpe", v: clamp(((bt.sharpe_ratio || 0) / 3) * 100) },
    { metric: "Sortino", v: clamp(((bt.sortino_ratio || 0) / 3) * 100) },
    { metric: "Profit Factor", v: clamp(((bt.profit_factor == null ? 2 : bt.profit_factor) / 2) * 100) },
    { metric: "Win Rate", v: clamp((bt.win_rate || 0) * 100) },
    { metric: "DD Headroom", v: clamp(((25 - (bt.max_drawdown || 0)) / 25) * 100) },
    { metric: "MC Survival", v: clamp((mc.survival_rate || 0) * 100) },
  ];
  const band = [
    { name: "Worst", v: mc.worst_return }, { name: "P5", v: mc.p5 }, { name: "Median", v: mc.median_return },
    { name: "P95", v: mc.p95 }, { name: "Best", v: mc.best_return },
  ].filter((d) => d.v !== undefined && d.v !== null);
  return (
    <div className="grid md:grid-cols-2 gap-4" data-testid={`risk-charts-${r.asset || ""}`}>
      <div data-testid="chart-equity-drawdown">
        <div className="text-[9px] text-term-muted mb-1">EQUITY + DRAWDOWN OVERLAY · {r.days}d · {r.candles} bars</div>
        <ResponsiveContainer width="100%" height={150}>
          <ComposedChart data={overlay} margin={{ top: 5, right: 6, left: -18, bottom: 0 }}>
            <CartesianGrid stroke="#111827" />
            <XAxis dataKey="i" tick={AX} hide />
            <YAxis yAxisId="l" tick={AX} /><YAxis yAxisId="r" orientation="right" tick={AX} />
            <Tooltip contentStyle={TT} />
            <Area yAxisId="r" dataKey="dd" name="drawdown %" stroke="#ff003c" fill="#ff003c22" strokeWidth={1} />
            <Line yAxisId="l" dataKey="equity" name="equity" stroke="#39ff14" dot={false} strokeWidth={1.5} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div data-testid="chart-risk-radar">
        <div className="text-[9px] text-term-muted mb-1">RISK-ADJUSTED PROFILE · normalised 0–100</div>
        <ResponsiveContainer width="100%" height={150}>
          <RadarChart data={radar} outerRadius={55}>
            <PolarGrid stroke="#1f2937" />
            <PolarAngleAxis dataKey="metric" tick={{ fontSize: 8, fill: "#9ca3af" }} />
            <Radar dataKey="v" stroke="#00e5ff" fill="#00e5ff33" strokeWidth={1.5} />
            <Tooltip contentStyle={TT} />
          </RadarChart>
        </ResponsiveContainer>
      </div>
      <div data-testid="chart-stress">
        <div className="text-[9px] text-term-muted mb-1">CRASH TESTS · worst-case loss %</div>
        <ResponsiveContainer width="100%" height={150}>
          <BarChart data={stress} margin={{ top: 5, right: 6, left: -18, bottom: 0 }}>
            <CartesianGrid stroke="#111827" />
            <XAxis dataKey="name" tick={{ fontSize: 7, fill: "#6b7280" }} interval={0} />
            <YAxis tick={AX} /><Tooltip contentStyle={TT} />
            <Bar dataKey="loss" name="loss %">
              {stress.map((s, i) => <Cell key={i} fill={s.survived ? "#ffd60a" : "#ff003c"} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div data-testid="chart-montecarlo">
        <div className="text-[9px] text-term-muted mb-1">MONTE CARLO · {mc.iterations || 0} runs · outcome distribution (return %)</div>
        <ResponsiveContainer width="100%" height={150}>
          <BarChart data={band} margin={{ top: 5, right: 6, left: -18, bottom: 0 }}>
            <CartesianGrid stroke="#111827" />
            <XAxis dataKey="name" tick={AX} /><YAxis tick={AX} /><Tooltip contentStyle={TT} />
            <Bar dataKey="v" name="return %">
              {band.map((d, i) => <Cell key={i} fill={(d.v || 0) >= 0 ? "#39ff14" : "#ff003c"} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

const pct = (v, d = 2) => (v === null || v === undefined ? "—" : `${Number(v).toFixed(d)}%`);
const num = (v, d = 2) => (v === null || v === undefined ? "∞" : Number(v).toFixed(d));

function Card({ label, value, cls = "text-white", testId }) {
  return (
    <div className="border border-term-border rounded p-2" data-testid={testId}>
      <div className="text-[9px] text-term-muted tracking-widest">{label}</div>
      <div className={`tnum text-sm font-bold ${cls}`}>{value}</div>
    </div>
  );
}

function AssetReport({ asset, r, onRun, running }) {
  const ok = r.status === "PROMOTE";
  const bt = r.backtest || {}, mc = r.monte_carlo || {}, ex = r.execution || {};
  return (
    <div className="panel p-3 space-y-3" data-testid={`risk-asset-${asset}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-white">{asset}</span>
          <span data-testid={`promotion-status-${asset}`} className={`text-[10px] px-2 py-0.5 rounded border font-bold ${ok ? "border-neon-green text-neon-green" : r.status === "NONE" ? "border-term-border text-term-muted" : "border-neon-red text-neon-red"}`}>
            {r.status === "NONE" ? "NOT VALIDATED" : r.status}{r.score !== undefined ? ` · ${r.score}` : ""}
          </span>
          <span className={`text-[9px] ${r.live_allowed ? "text-neon-green" : "text-neon-amber"}`} data-testid={`live-gate-${asset}`}>
            {r.live_allowed ? <ShieldCheck size={12} className="inline" /> : <ShieldAlert size={12} className="inline" />} LIVE {r.live_allowed ? "ALLOWED" : "BLOCKED"} · {r.live_reason}
          </span>
          {Object.entries(r.timeframes || {}).map(([tf, t]) => (
            <span key={tf} data-testid={`tf-${asset}-${tf}`} className={`text-[9px] px-1.5 py-0.5 border rounded ${t.status === "PROMOTE" ? "border-neon-green text-neon-green" : "border-term-border text-term-muted"}`}>{tf} {t.status === "PROMOTE" ? "✓" : "✗"} {t.score}</span>
          ))}
        </div>
        <button data-testid={`run-validation-${asset}`} disabled={running} onClick={() => onRun(asset)}
          className="text-[10px] px-2 py-1 border border-neon-cyan text-neon-cyan hover:bg-neon-cyan hover:text-black transition-colors disabled:opacity-40 flex items-center gap-1">
          <Play size={10} /> {running ? "RUNNING…" : "VALIDATE"}
        </button>
      </div>
      {r.status !== "NONE" && (
        <>
          <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
            <Card label="SHARPE" value={num(bt.sharpe_ratio)} cls={bt.sharpe_ratio >= 1 ? "text-neon-green" : "text-neon-red"} />
            <Card label="SORTINO" value={num(bt.sortino_ratio)} />
            <Card label="PF" value={num(bt.profit_factor)} cls={bt.profit_factor === null || bt.profit_factor >= 1.2 ? "text-neon-green" : "text-neon-red"} />
            <Card label="MAX DD" value={pct(bt.max_drawdown)} cls={bt.max_drawdown <= 25 ? "text-neon-green" : "text-neon-red"} />
            <Card label="WIN" value={pct((bt.win_rate || 0) * 100, 0)} />
            <Card label="TRADES" value={bt.trades ?? "—"} />
            <Card label="MC SURVIVAL" value={pct((mc.survival_rate || 0) * 100, 1)} cls={mc.survival_rate >= 0.95 ? "text-neon-green" : "text-neon-red"} />
            <Card label="MC RUIN" value={pct((mc.ruin_probability || 0) * 100, 1)} />
            <Card label="MC MEDIAN" value={pct(mc.median_return)} />
            <Card label="MC P5 / P95" value={`${pct(mc.p5, 1)} / ${pct(mc.p95, 1)}`} />
            <Card label="SLIPPAGE" value={`${num(ex.slippage_bps, 1)} bps`} />
            <Card label="HEALTH" value={`${r.health?.score ?? "—"} ${r.health?.band || ""}`} cls={r.health?.band === "SAFE" ? "text-neon-green" : r.health?.band === "CRITICAL" ? "text-neon-red" : "text-neon-amber"} testId={`health-${asset}`} />
          </div>
          <RiskCharts r={{ ...r, asset }} />
          <div>
            <div className="text-[9px] text-term-muted mb-1">BENCHMARKS · strategy {pct(r.benchmark?.strategy_return)}</div>
            <div className="flex flex-wrap gap-2 text-[10px]">
              {Object.entries(r.benchmark?.benchmarks || {}).map(([k, v]) => (
                <span key={k} className="border border-term-border rounded px-2 py-1 text-term-muted">{k} {pct(v.return)} · α <span className={v.alpha >= 0 ? "text-neon-green" : "text-neon-red"}>{pct(v.alpha)}</span>{v.beta !== null && v.beta !== undefined ? ` · β ${num(v.beta)} · IR ${num(v.information_ratio)}` : ""}</span>
              ))}
            </div>
          </div>
          <div>
            <div className="text-[9px] text-term-muted mb-1">GATE CHECKS · {(r.checks || []).filter((c) => c.passed).length}/{(r.checks || []).length} passed · replay {r.replay_ok ? "OK" : "MISMATCH"} · {r.events} events</div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-1 text-[10px]">
              {(r.checks || []).map((c) => (
                <div key={c.check} data-testid={`check-${asset}-${c.check}`} className={`flex justify-between px-2 py-0.5 border rounded ${c.passed ? "border-term-border text-term-muted" : "border-neon-red/60 text-neon-red"}`}>
                  <span>{c.check}</span><span className="tnum">{c.value === null ? "∞" : c.value} {c.op} {c.threshold}</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default function RiskPage() {
  const nav = useNavigate();
  const [data, setData] = useState(null);
  const [live, setLive] = useState(null);
  const [jobs, setJobs] = useState({});
  const load = () => {
    api.get("/research/latest").then((r) => setData(r.data)).catch(() => {});
    api.get("/research/live-risk").then((r) => setLive(r.data)).catch(() => {});
    api.get("/research/status").then((r) => setJobs(r.data.jobs || {})).catch(() => {});
  };
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);
  const run = async (asset) => {
    try { await api.post(`/research/run/${asset}?days=30&timeframe=all`); toast.success(`${asset} validation queued · 1m, 5m, 15m → best passing timeframe goes live`); load(); }
    catch (e) { toast.error(e.response?.data?.detail?.message || "cannot start"); }
  };
  const rollback = async () => {
    if (!window.confirm("Roll back LUCHII_003? Live entries will be blocked until a new validation passes.")) return;
    await api.post("/research/rollback", { reasons: ["manual rollback from Risk dashboard"] }); toast("Rolled back"); load();
  };
  return (
    <div className="min-h-screen bg-black text-white p-4 space-y-4" data-testid="risk-page">
      <div className="flex items-center justify-between">
        <button data-testid="risk-back" onClick={() => nav("/")} className="text-term-muted hover:text-white text-xs flex items-center gap-1"><ArrowLeft size={12} /> TERMINAL</button>
        <h1 className="text-base md:text-lg font-bold tracking-widest">RISK · PROMOTION PIPELINE <span className="text-[10px] text-neon-cyan font-normal" data-testid="active-timeframe">LIVE TF {data?.active_timeframe || "1m"}</span></h1>
        <button data-testid="rollback-button" onClick={rollback} className="text-[10px] px-2 py-1 border border-neon-red text-neon-red hover:bg-neon-red hover:text-black flex items-center gap-1"><RotateCcw size={10} /> ROLLBACK</button>
      </div>
      <p className="text-[10px] text-term-muted">No strategy reaches OKX unless it passes backtest v2 (real fills, fees, slippage) + Monte Carlo + correlated crash tests + risk gates (<code>risk_gates.yaml</code>). Past results never guarantee future returns.</p>
      {live && (
        <div className="grid grid-cols-3 md:grid-cols-7 gap-2" data-testid="live-risk-card">
          <Card label="EQUITY" value={`$${num(live.equity)}`} />
          <Card label="DRAWDOWN" value={pct(live.drawdown_pct)} cls={live.drawdown_pct > 10 ? "text-neon-red" : "text-neon-green"} />
          <Card label="EXPOSURE" value={`$${num(live.gross_exposure)}`} />
          <Card label="LEVERAGE" value={`${num(live.leverage)}×`} />
          <Card label="OPEN POS" value={live.open_positions} />
          <Card label="KILL SWITCH" value={live.kill_switch ? "ARMED" : "OFF"} cls={live.kill_switch ? "text-neon-red" : "text-neon-green"} />
          <Card label="HEALTH" value={`${live.health?.score} ${live.health?.band}`} cls={live.health?.band === "SAFE" ? "text-neon-green" : "text-neon-amber"} testId="live-health" />
          {live.rollback_triggers?.length > 0 && <div className="col-span-full text-[10px] text-neon-red">ROLLBACK POLICY BREACHED · {live.rollback_triggers.join(" · ")}</div>}
        </div>
      )}
      {data ? ["BTC", "ETH", "SOL"].map((a) => <AssetReport key={a} asset={a} r={data.assets[a]} onRun={run} running={Object.entries(jobs).some(([k, j]) => k.startsWith(a) && ["queued", "running"].includes(j.status))} />) : <div className="text-term-muted text-xs">loading…</div>}
      {data?.history?.length > 0 && (
        <div className="panel p-3">
          <div className="text-[9px] text-term-muted mb-1">PROMOTION HISTORY</div>
          <div className="space-y-0.5 text-[10px]">
            {data.history.map((h) => (
              <div key={h.id} className="flex gap-3 text-term-muted" data-testid="promotion-history-row">
                <span className="tnum">{new Date(h.ts).toLocaleString()}</span><span className="text-white">{h.asset || "ALL"}</span>
                <span className={h.status === "PROMOTE" ? "text-neon-green" : "text-neon-red"}>{h.status}</span><span>{h.version}</span><span className="truncate">{(h.reasons || []).slice(0, 2).join("; ")}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
