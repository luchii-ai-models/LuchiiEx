import { useEffect, useState } from "react";
import api from "../../lib/api";

export default function StreakCard({ leaderboard, bot, user }) {
  const [pred, setPred] = useState({ n: 0, won: 0, net: 0 });
  useEffect(() => {
    const load = () => api.get("/predict/mine").then((r) => setPred(r.data)).catch(() => {});
    load(); const t = setInterval(load, 10000); return () => clearInterval(t);
  }, []);
  const streak = bot?.wins ?? 0;
  const tradePnl = bot?.realized_pnl ?? 0;
  const pnl = tradePnl + (pred.net || 0);
  const wins = (bot?.wins ?? 0) + (pred.won || 0);
  const total = (bot?.trades_count ?? 0) + (pred.n || 0);
  const winRate = total ? Math.round((wins / total) * 100) : 0;
  const top = leaderboard || [];
  const bars = Array.from({ length: 22 }, (_, i) => 20 + ((Math.sin(i * 0.9) + 1) / 2) * 60 + i * 0.6);

  return (
    <div className="panel panel-glow p-3 shrink-0" data-testid="streak-card">
      <div className="flex items-center justify-between">
        <span className="panel-label">◈ #1 TRADER · STREAK</span>
        <span className="px-1.5 py-0.5 text-[9px] tracking-[0.2em] border border-neon-magenta text-neon-magenta">RANK</span>
      </div>
      <div className="flex items-end gap-3 mt-2">
        <div className="leading-none">
          <span className="text-term-muted text-xs">×</span>
          <span className="tnum text-4xl font-extrabold text-neon-magenta text-glow-magenta">{Math.max(1, streak + (pred.won || 0))}</span>
          <span className="tnum text-neon-green text-glow-green text-sm ml-1">+{total}</span>
        </div>
        <svg viewBox="0 0 120 46" className="flex-1 h-12">
          {bars.map((b, i) => (
            <rect key={i} x={i * 5.4} y={46 - (b / 100) * 46} width="3.6" height={(b / 100) * 46}
              fill="#ffd60a" opacity={0.35 + (i / bars.length) * 0.65} />
          ))}
        </svg>
      </div>
      <div className="grid grid-cols-2 gap-2 mt-3">
        <div className="border border-term-border p-1.5">
          <div className="panel-label text-[8px]">REALIZED · ALL PRODUCTS</div>
          <div className={`tnum text-sm font-bold ${pnl >= 0 ? "text-neon-green" : "text-neon-red"}`} data-testid="streak-realized">
            {pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}
          </div>
          <div className="text-[8px] text-term-muted tnum">trade {tradePnl >= 0 ? "+" : ""}{tradePnl.toFixed(2)} · predict {pred.net >= 0 ? "+" : ""}{(pred.net || 0).toFixed(2)}</div>
        </div>
        <div className="border border-term-border p-1.5">
          <div className="panel-label text-[8px]">WIN RATE</div>
          <div className="tnum text-sm font-bold text-neon-cyan" data-testid="streak-winrate">{winRate}%</div>
          <div className="text-[8px] text-term-muted tnum">predict {pred.won}/{pred.n}</div>
        </div>
      </div>
      <div className="mt-2 border-t border-term-border pt-1.5 space-y-0.5 max-h-20 overflow-y-auto">
        {top.slice(0, 4).map((r) => (
          <div key={r.rank} className="flex justify-between text-[10px] tnum" data-testid="leaderboard-row">
            <span className="text-term-muted">#{r.rank} {r.name}{r.predictN ? <span className="text-neon-magenta"> ·P{r.predictWon}/{r.predictN}</span> : null}</span>
            <span className={r.pnl >= 0 ? "text-neon-green" : "text-neon-red"}>{r.pnl >= 0 ? "+" : ""}{r.pnl}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
