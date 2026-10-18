import { useEffect, useState } from "react";
import { TrendingUp, TrendingDown } from "lucide-react";
import api from "../../lib/api";
import { toast } from "sonner";

export default function PredictPanel() {
  const [st, setSt] = useState(null);
  const [asset, setAsset] = useState("BTC");
  const [dur, setDur] = useState(30);
  const [stake, setStake] = useState(1);
  const [rows, setRows] = useState({ items: [], stats: {} });
  const load = () => {
    api.get("/predict/status").then((r) => setSt(r.data)).catch(() => {});
    api.get("/predict/contracts").then((r) => setRows(r.data)).catch(() => {});
  };
  useEffect(() => { load(); const t = setInterval(load, 2000); return () => clearInterval(t); }, []);
  const place = async (direction) => {
    try {
      const r = await api.post("/predict/contracts", { asset, direction, stake: Number(stake), duration: dur, idempotencyKey: crypto.randomUUID() });
      toast.success(`${asset} ${direction} · $${r.data.stake} → $${r.data.potentialPayout} in ${dur}s`); load();
    } catch (e) { toast.error(e.response?.data?.detail?.message || "rejected"); }
  };
  if (!st) return null;
  const q = st.quotes?.[asset] || {};
  const mult = q.payouts?.[dur];
  return (
    <div className="space-y-3" data-testid="predict-panel">
      <div className="text-[10px] text-neon-amber border border-neon-amber/40 rounded p-2" data-testid="predict-notice">{st.notice}{!st.enabled && " · DISABLED ON THIS SERVER (PREDICT_ENABLED)"}</div>
      {st.enabled && (
        <div className="text-[10px] border border-neon-green/40 rounded p-2 text-neon-green" data-testid="auto-predict-status">
          AI AUTO-PREDICT {st.autoPredict ? "ON" : "OFF"} · places {st.autoRules.duration}s contracts on its own when conviction ≥ {Math.round(st.autoRules.minConfidence * 100)}% · stake {st.autoRules.stakePct * 100}% of wallet (max ${st.autoRules.maxStake}) · stops after a {st.autoRules.dailyLossPct * 100}% daily loss · works from $1
          {Object.entries(st.quotes).some(([, q]) => q.aiSignal) && <div className="text-white mt-1">signal now: {Object.entries(st.quotes).filter(([, q]) => q.aiSignal).map(([a, q]) => `${a} ${q.aiSignal}`).join(" · ")}</div>}
        </div>
      )}
      <div className="flex flex-wrap gap-1">
        {st.assets.map((a) => <button key={a} data-testid={`predict-asset-${a}`} onClick={() => setAsset(a)} className={`text-[10px] px-2 py-1 border ${asset === a ? "border-neon-cyan text-neon-cyan" : "border-term-border text-term-muted"}`}>{a}</button>)}
        <span className="text-[10px] text-term-muted self-center ml-2 tnum">${q.price?.toLocaleString()} · AI conf {Math.round((q.confidence || 0) * 100)}%</span>
      </div>
      <div className="flex flex-wrap gap-1">
        {st.durations.map((d) => <button key={d} data-testid={`predict-dur-${d}`} onClick={() => setDur(d)} className={`text-[10px] px-2 py-1 border ${dur === d ? "border-neon-magenta text-neon-magenta" : "border-term-border text-term-muted"}`}>{d >= 60 ? `${d / 60}m` : `${d}s`}</button>)}
      </div>
      <div className="flex items-center gap-2">
        <input data-testid="predict-stake-input" type="number" min={st.minStake} max={st.maxStake} value={stake} onChange={(e) => setStake(e.target.value)} className="w-24 bg-black border border-term-border px-2 py-1 text-xs tnum" />
        <span className="text-[10px] text-term-muted">PAYOUT <span className="text-white font-bold">{mult}×</span> → ${(Number(stake) * (mult || 0)).toFixed(2)}</span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <button data-testid="predict-up-button" disabled={!st.enabled} onClick={() => place("UP")} className="py-2 border border-neon-green text-neon-green hover:bg-neon-green hover:text-black disabled:opacity-40 text-xs flex items-center justify-center gap-1"><TrendingUp size={12} /> UP</button>
        <button data-testid="predict-down-button" disabled={!st.enabled} onClick={() => place("DOWN")} className="py-2 border border-neon-red text-neon-red hover:bg-neon-red hover:text-black disabled:opacity-40 text-xs flex items-center justify-center gap-1"><TrendingDown size={12} /> DOWN</button>
      </div>
      <div className="text-[10px] text-term-muted" data-testid="predict-stats">settled {rows.stats.settled || 0} · won {rows.stats.won || 0} · net <span className={(rows.stats.net || 0) >= 0 ? "text-neon-green" : "text-neon-red"}>${(rows.stats.net || 0).toFixed(2)}</span></div>
      <div className="space-y-0.5 max-h-40 overflow-auto">
        {rows.items.map((c) => (
          <div key={c.id} data-testid="predict-row" className="flex justify-between text-[10px] border-b border-term-border/50 py-0.5">
            <span className="text-white">{c.asset} {c.direction} {c.duration}s</span>
            <span className="tnum text-term-muted">${c.stake} @ {c.entryPrice?.toLocaleString()}</span>
            <span className={`font-bold ${c.status === "WON" ? "text-neon-green" : c.status === "LOST" ? "text-neon-red" : c.status === "OPEN" ? "text-neon-cyan" : "text-term-muted"}`}>{c.status}{c.status === "WON" ? ` +$${(c.payout - c.stake).toFixed(2)}` : ""}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
