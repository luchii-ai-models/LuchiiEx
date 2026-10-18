import { useEffect, useRef, useState } from "react";
import { Play, Square, RotateCcw, Ban, Zap, ArrowLeftRight } from "lucide-react";
import api from "../../lib/api";
import { toast } from "sonner";

function StrategyToggle({ bot, refresh }) {
  const [busy, setBusy] = useState(false);
  const cur = bot.strategy ?? "luchii003";
  const aggr = cur === "aggressive";
  const flip = async () => {
    const next = aggr ? "luchii003" : "aggressive";
    setBusy(true);
    try {
      await api.post("/bot/config", { strategy: next, aggressive: next === "aggressive" });
      toast.success(next === "aggressive" ? "AGGRESSIVE SCALP · fewer filters, more trades, more fee risk" : "LUCHII_003 · strict filters, trades only on A-grade setups");
      refresh();
    } catch (e) { toast.error(e.response?.data?.detail || "Switch failed"); }
    finally { setBusy(false); }
  };
  return (
    <button data-testid="strategy-toggle" onClick={flip} disabled={busy} title="Tap to switch strategy · applies to the next entry, open positions keep being managed"
      className={`mt-1 w-full flex items-center justify-between px-2 py-1 border text-[9px] tracking-[0.18em] transition-colors disabled:opacity-40 ${aggr ? "border-neon-amber text-neon-amber hover:bg-neon-amber/10" : "border-neon-cyan text-neon-cyan hover:bg-neon-cyan/10"}`}>
      <span className="flex items-center gap-1"><Zap size={9} /> {aggr ? "AGGRESSIVE SCALP" : "LUCHII_003"}</span>
      <span className="flex items-center gap-1 text-term-muted"><ArrowLeftRight size={9} /> {aggr ? "LUCHII_003" : "AGGRESSIVE"}</span>
    </button>
  );
}


export default function BotStatusCard({ bot, refresh, highlightKill }) {
  const killRef = useRef(null);
  useEffect(() => {
    if (highlightKill && killRef.current) {
      killRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [highlightKill]);
  if (!bot) return <Panel />;
  const live = bot.status === "running";
  const equity = bot.equity ?? 0;
  const pnl = bot.realized_pnl ?? 0;
  const inPos = (bot.position_qty ?? 0) > 0;
  const alts = Object.entries(bot.alt_positions || {}).filter(([, p]) => (p?.qty ?? 0) > 0);

  const act = async (fn, ok) => {
    try { await fn(); toast.success(ok); refresh(); }
    catch (e) { toast.error(e.response?.data?.detail || "Action failed"); }
  };

  return (
    <div className="panel panel-glow animate-pulse-glow p-3 shrink-0">
      <div className="flex items-center justify-between">
        <span className="panel-label">◈ ACTIVE</span>
        <div className="flex items-center gap-1.5">
          {bot.mode === "live"
            ? <span data-testid="bot-mode-badge" className="px-1.5 py-0.5 text-[9px] tracking-[0.2em] border border-neon-red text-neon-red">REAL</span>
            : <span data-testid="bot-mode-badge" className="px-1.5 py-0.5 text-[9px] tracking-[0.2em] border border-neon-cyan text-neon-cyan">DRY</span>}
          <span className={`px-1.5 py-0.5 text-[9px] tracking-[0.2em] border ${live ? "border-neon-green text-neon-green" : "border-term-muted text-term-muted"}`}>
            {live ? "LIVE" : "STOPPED"}
          </span>
        </div>
      </div>
      <div className="mt-1 text-lg font-extrabold tracking-tighter">{bot.name}</div>
      <StrategyToggle bot={bot} refresh={refresh} />
      <div className="panel-label text-[9px]">EQUITY (USD)</div>
      <div data-testid="bot-equity" className="tnum text-4xl font-extrabold text-neon-magenta text-glow-magenta leading-none mt-1">
        ${equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </div>

      <div className="grid grid-cols-3 gap-2 mt-3 text-center">
        <Mini label="TRADES" value={bot.trades_count} />
        <Mini label="WIN%" value={`${bot.win_rate}%`} cls="text-neon-green" />
        <Mini label="P&L" value={`${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}`} cls={pnl >= 0 ? "text-neon-green" : "text-neon-red"} />
      </div>

      <div className="mt-2 flex items-center justify-between text-[10px] text-term-muted">
        <span data-testid="bot-positions">POS: {inPos || alts.length ? (
          <span className="text-neon-green">
            {inPos && `${bot.position_qty} BTC @ $${bot.entry_price?.toFixed(0)}`}
            {alts.map(([a, p]) => ` · ${p.qty} ${a} @ $${Number(p.entry).toFixed(2)}`)}
          </span>
        ) : <span>FLAT</span>}</span>
        <span>{bot.strategy === "aggressive" ? (bot.size_pct ? `${bot.size_pct}%/TRADE` : `SIZE $${bot.position_usd}`) : "RISK 1–1.5%/TRADE"}{bot.manual_confirm ? " · MC" : ""}</span>
      </div>

      {bot.kill_switch && (
        <div className="mt-2 flex items-center justify-between border border-neon-red/50 bg-neon-red/10 px-2 py-1.5">
          <span className="text-[10px] text-neon-red tracking-[0.15em]">🛑 KILL SWITCH ENGAGED</span>
          <button data-testid="rearm-button" onClick={() => act(() => api.post("/bot/kill/reset"), "Re-armed")}
            className="text-[10px] text-neon-cyan hover:text-glow-cyan underline">RE-ARM</button>
        </div>
      )}

      <div className="grid grid-cols-4 gap-1.5 mt-3">
        {!live ? (
          bot.autopilot === false ? (
            <button data-testid="bot-resume-button" disabled={bot.kill_switch} onClick={() => act(() => api.post("/bot/resume"), "Autopilot resumed")}
              className="col-span-2 border border-neon-green text-neon-green hover:bg-neon-green hover:text-black transition-colors py-2 text-xs tracking-[0.15em] flex items-center justify-center gap-1 disabled:opacity-30">
              <Play size={12} /> RESUME AUTOPILOT
            </button>
          ) : (
            <div data-testid="bot-autopilot-waiting" className="col-span-2 border border-term-border text-term-muted py-2 text-[10px] tracking-[0.12em] flex items-center justify-center gap-1 text-center">
              <Zap size={10} /> AUTOPILOT · {bot.mode === "live" ? "starts when funds land" : "connect exchange"}
            </div>
          )
        ) : (
          <button data-testid="bot-stop-button" onClick={() => act(() => api.post("/bot/stop"), "Autopilot paused")}
            className="col-span-2 border border-neon-red text-neon-red hover:bg-neon-red hover:text-black transition-colors py-2 text-xs tracking-[0.15em] flex items-center justify-center gap-1">
            <Square size={12} /> PAUSE
          </button>
        )}
        <button data-testid="bot-kill-button" ref={killRef} onClick={() => act(() => api.post("/bot/kill"), "Kill switch engaged")}
          className={`border border-neon-red/60 text-neon-red hover:bg-neon-red hover:text-black transition-colors py-2 text-xs flex items-center justify-center ${highlightKill ? "flash-highlight" : ""}`} title="Kill switch">
          <Ban size={12} />
        </button>
        <button data-testid="bot-reset-button" onClick={() => act(() => api.post("/bot/reset"), "Engine reset")}
          className="border border-term-border text-term-muted hover:border-neon-cyan hover:text-neon-cyan transition-colors py-2 text-xs flex items-center justify-center" title="Reset">
          <RotateCcw size={12} />
        </button>
      </div>
    </div>
  );
}

const Mini = ({ label, value, cls = "text-white" }) => (
  <div className="border border-term-border py-1">
    <div className="panel-label text-[8px]">{label}</div>
    <div className={`tnum text-sm font-bold ${cls}`}>{value}</div>
  </div>
);

const Panel = () => <div className="panel p-3 h-40 animate-pulse shrink-0" />;
