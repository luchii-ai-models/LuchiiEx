import { useEffect, useState } from "react";
import { RefreshCw, ShieldCheck, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../../lib/api";

const err = (e) => toast.error(formatApiErrorDetail(e.response?.data?.detail));
const field = "w-full bg-transparent border-b border-neon-magenta/60 focus:border-neon-magenta outline-none py-2 text-white text-sm";

function Perm({ name, ok }) {
  return (
    <span data-testid={`perm-${name}`} className={`px-2 py-0.5 border text-[9px] tracking-[0.12em] ${ok ? "border-neon-green text-neon-green" : "border-neon-red text-neon-red line-through"}`}>
      {name.toUpperCase()}
    </span>
  );
}

function Capabilities({ cap }) {
  if (!cap) return null;
  const p = cap.permissions;
  return (
    <div data-testid="exchange-capabilities" className="border border-term-border p-3 space-y-2">
      <div className="flex items-center gap-2 panel-label">
        {p && !p.trade ? <ShieldAlert size={12} className="text-neon-red" /> : <ShieldCheck size={12} className="text-neon-green" />}
        KEY CAPABILITIES · {cap.symbol}{cap.hostname ? ` · ${cap.hostname}` : ""}
      </div>
      {p ? (
        <div className="flex flex-wrap gap-1.5">
          {["read", "trade", "withdraw", "transfer"].map((k) => <Perm key={k} name={k} ok={p[k]} />)}
        </div>
      ) : <p className="text-[9px] text-term-muted">This exchange doesn't report key permissions; orders show an error if a permission is missing.</p>}
      {cap.warnings.map((w) => <p key={w} data-testid="capability-warning" className="text-[10px] text-neon-red">{w}</p>)}
    </div>
  );
}

export default function ExchangeTools({ refresh }) {
  const [cap, setCap] = useState(null);
  const [sync, setSync] = useState(null);
  const [usd, setUsd] = useState("");
  const [qty, setQty] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.get("/exchange/capabilities").then((r) => setCap(r.data)).catch(err); }, []);

  const run = async (fn) => { setBusy(true); try { await fn(); } catch (e) { err(e); } finally { setBusy(false); } };
  const doSync = () => run(async () => { const { data } = await api.post("/exchange/sync"); setSync(data); refresh(); toast.success(`Synced · equity $${data.equity}`); });
  const order = (side) => run(async () => {
    const { data } = await api.post("/exchange/order", side === "BUY" ? { side, usd: Number(usd) || undefined } : { side, qty: Number(qty) || undefined });
    toast.success(`${side} filled #${data.id} · ${data.filled} BTC @ ${data.average}`); refresh();
  });

  return (
    <div className="space-y-3">
      <Capabilities cap={cap} />
      <div className="border border-term-border p-3 space-y-2">
        <div className="flex items-center justify-between">
          <span className="panel-label">ACCOUNT SYNC</span>
          <button data-testid="exchange-sync-button" onClick={doSync} disabled={busy}
            className="border border-neon-cyan text-neon-cyan hover:bg-neon-cyan hover:text-black transition-colors px-3 py-1 text-[10px] flex items-center gap-1 disabled:opacity-30">
            <RefreshCw size={11} className={busy ? "animate-spin" : ""} /> SYNC
          </button>
        </div>
        {sync && Object.entries(sync.balances).map(([acc, b]) => (
          <div key={acc} data-testid={`sync-account-${acc}`} className="text-[10px] tnum">
            <div className="text-term-muted uppercase">{acc} account</div>
            {Object.keys(b).length === 0 && <div className="text-term-muted">empty</div>}
            {Object.entries(b).map(([k, v]) => <div key={k} className="flex justify-between"><span>{k}</span><span className="text-white">{v}</span></div>)}
          </div>
        ))}
      </div>
      <div className="border border-term-border p-3 space-y-2">
        <span className="panel-label">MANUAL TRADE · MARKET</span>
        <div className="grid grid-cols-2 gap-2">
          <input data-testid="manual-buy-usd-input" type="number" placeholder="USD (default size)" value={usd} onChange={(e) => setUsd(e.target.value)} className={field} />
          <input data-testid="manual-sell-qty-input" type="number" placeholder="BTC (default all)" value={qty} onChange={(e) => setQty(e.target.value)} className={field} />
          <button data-testid="manual-buy-button" onClick={() => order("BUY")} disabled={busy}
            className="border border-neon-green text-neon-green hover:bg-neon-green hover:text-black transition-colors py-2 text-xs tracking-[0.15em] disabled:opacity-30">BUY BTC</button>
          <button data-testid="manual-sell-button" onClick={() => order("SELL")} disabled={busy}
            className="border border-neon-red text-neon-red hover:bg-neon-red hover:text-black transition-colors py-2 text-xs tracking-[0.15em] disabled:opacity-30">SELL BTC</button>
        </div>
      </div>
    </div>
  );
}
