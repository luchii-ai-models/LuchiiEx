import { useEffect, useRef } from "react";
import { Play, Square, RotateCcw, Ban } from "lucide-react";
import api from "../../lib/api";
import { toast } from "sonner";

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
        <span className="panel-label">◈ ACTIVE ENGINE</span>
        <span className={`px-1.5 py-0.5 text-[9px] tracking-[0.2em] border ${live ? "border-neon-green text-neon-green" : "border-term-muted text-term-muted"}`}>
          {live ? "LIVE" : "STOPPED"}
        </span>
      </div>
      <div className="mt-1 text-lg font-extrabold tracking-tighter">{bot.name}</div>
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
        <span>{bot.size_pct ? `${bot.size_pct}%/TRADE` : `SIZE $${bot.position_usd}`}{bot.manual_confirm ? " · MC" : ""}</span>
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
          <button data-testid="bot-start-button" disabled={bot.kill_switch} onClick={() => act(() => api.post("/bot/start"), "Engine engaged")}
            className="col-span-2 border border-neon-green text-neon-green hover:bg-neon-green hover:text-black transition-colors py-2 text-xs tracking-[0.15em] flex items-center justify-center gap-1 disabled:opacity-30">
            <Play size={12} /> START
          </button>
        ) : (
          <button data-testid="bot-stop-button" onClick={() => act(() => api.post("/bot/stop"), "Engine halted")}
            className="col-span-2 border border-neon-red text-neon-red hover:bg-neon-red hover:text-black transition-colors py-2 text-xs tracking-[0.15em] flex items-center justify-center gap-1">
            <Square size={12} /> STOP
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
