import { useEffect, useState } from "react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../../lib/api";

const OK = (v) => (v === "APPROVED" ? "text-neon-green" : v === "REJECTED" ? "text-neon-red" : "text-yellow-400");

export default function ValidationCard({ assets: a0 }) {
  const assets = a0 && a0.length ? a0 : ["BTC", "ETH", "SOL"];
  const [agents, setAgents] = useState(null);
  const [mc, setMc] = useState(null);
  const [wf, setWf] = useState({});
  const [asset, setAsset] = useState("BTC");
  const [busy, setBusy] = useState(false);
  const load = () => {
    api.get("/strategy/agents").then((r) => setAgents(r.data)).catch(() => {});
    api.get("/strategy/montecarlo").then((r) => setMc(r.data)).catch(() => {});
    api.get(`/strategy/walkforward/${asset}`).then((r) => setWf(r.data)).catch(() => {});
  };
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [asset]); // eslint-disable-line react-hooks/exhaustive-deps
  const runWf = async () => {
    setBusy(true);
    try { await api.post(`/strategy/walkforward/${asset}?days=30`); toast.success(`30-day walk-forward queued for ${asset} (takes ~1-2 min)`); load(); }
    catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); } finally { setBusy(false); }
  };
  const a = agents?.assets?.[asset];
  const r = wf.result;
  return (
    <div className="panel p-3" data-testid="validation-card">
      <div className="flex justify-between items-center mb-2">
        <span className="panel-label">AGENT VOTE · VALIDATION</span>
        <div className="flex gap-1">
          {assets.map((s) => (
            <button key={s} data-testid={`validation-asset-${s}`} onClick={() => setAsset(s)}
              className={`text-[9px] px-1.5 py-0.5 border ${asset === s ? "border-neon-green text-neon-green" : "border-term-border text-term-muted"}`}>{s}</button>
          ))}
        </div>
      </div>
      {a && (
        <div data-testid="agent-votes" className="text-[9px] text-term-muted">
          <div className="grid grid-cols-5 gap-1">
            {Object.entries(a.scores || {}).map(([k, v]) => (
              <span key={k} className={k === a.winner ? "text-white" : ""}>{k.slice(0, 5).toUpperCase()} <b>{v}</b></span>
            ))}
          </div>
          <p className="mt-1">final <b className={a.allowed ? "text-neon-green" : "text-white"} data-testid="agent-final">{a.final}</b> / {agents.min_final} · regime {a.regime || "—"} · {a.reason}</p>
        </div>
      )}
      <div className="mt-2 border-t border-term-border pt-1 text-[9px] text-term-muted" data-testid="montecarlo">
        <span className="text-white">MONTE CARLO (live trades)</span>
        {mc?.simulations ? (
          <p>ruin <b className="text-white">{mc.probability_of_ruin}%</b> · DD95 <b className="text-white">{mc.drawdown_95}%</b> · worst streak <b className="text-white">{mc.worst_loss_streak}</b> · {mc.trades} trades · <b className={OK(mc.deployment)}>{mc.deployment}</b></p>
        ) : <p>{mc?.reason || "need closed trades"}</p>}
      </div>
      <div className="mt-2 border-t border-term-border pt-1 text-[9px] text-term-muted" data-testid="walkforward">
        <div className="flex justify-between items-center">
          <span className="text-white">WALK-FORWARD 30D · {asset}</span>
          <button data-testid="run-walkforward" onClick={runWf} disabled={busy || ["queued", "running"].includes(wf.job?.status)}
            className="border border-term-border px-2 py-0.5 hover:text-white disabled:opacity-50">
            {["queued", "running"].includes(wf.job?.status) ? `${wf.job.status}…` : "RUN"}
          </button>
        </div>
        {wf.job?.status === "error" && <p className="text-neon-red">{wf.job.error}</p>}
        {r && (
          <p data-testid="walkforward-result">
            {r.walk_forward.windows.length} windows · avg PF <b className="text-white">{r.walk_forward.avg_pf}</b> · win {r.walk_forward.avg_win_rate}% · DD {r.walk_forward.max_drawdown}% ·
            MC ruin {r.monte_carlo.probability_of_ruin ?? "—"}% · <b className={OK(r.deployment)}>{r.deployment}</b> · {new Date(r.ts).toLocaleDateString()}
          </p>
        )}
      </div>
    </div>
  );
}
