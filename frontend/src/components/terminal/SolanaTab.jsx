import { useEffect, useState } from "react";
import { Search, Trash2 } from "lucide-react";
import api, { formatApiErrorDetail } from "../../lib/api";
import { toast } from "sonner";

const Label = ({ children }) => <div className="panel-label mb-2">{children}</div>;
const Input = (props) => (
  <input {...props} className="w-full bg-transparent border-b border-neon-magenta/60 focus:border-neon-magenta outline-none py-2 text-white text-sm" />
);
const Btn = ({ children, color = "neon-magenta", ...p }) => (
  <button {...p} className={`w-full border border-${color} text-${color} hover:bg-${color} hover:text-black transition-colors py-2 text-xs tracking-[0.2em] disabled:opacity-30`}>{children}</button>
);
const err = (e) => toast.error(formatApiErrorDetail(e.response?.data?.detail) || "Request failed");

export default function SolanaTab() {
  const [w, setW] = useState(null);
  const load = () => api.get("/solana/wallet").then((r) => setW(r.data)).catch(() => setW({ connected: false }));
  useEffect(() => { load(); }, []);
  if (!w) return <p className="text-[10px] text-term-muted">Loading…</p>;
  return w.connected ? <SolWallet w={w} reload={load} /> : <SolImport reload={load} />;
}

function SolImport({ reload }) {
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try { const r = await api.post("/solana/wallet/import", { secret }); toast.success(`Wallet ${r.data.pubkey.slice(0, 8)}… imported`); setSecret(""); reload(); }
    catch (e) { err(e); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-5" data-testid="solana-import">
      <p className="text-[10px] text-term-muted">Import a Solana wallet so the bot can swap SOL into your tokens on every BUY and back to SOL on every SELL (Jupiter DEX, mainnet). The key is encrypted at rest and never shown again.</p>
      <div>
        <Label>SOLANA PRIVATE KEY (BASE58 OR JSON ARRAY)</Label>
        <textarea data-testid="solana-secret-input" value={secret} onChange={(e) => setSecret(e.target.value)} rows={3}
          className="w-full bg-transparent border border-neon-magenta/60 focus:border-neon-magenta outline-none p-2 text-white text-xs font-mono" placeholder="Export from Phantom → Settings → Export private key" />
      </div>
      <Btn data-testid="solana-import-button" onClick={go} disabled={busy || secret.length < 20}>{busy ? "IMPORTING…" : "IMPORT WALLET"}</Btn>
    </div>
  );
}

function SolWallet({ w, reload }) {
  const [amount, setAmount] = useState(w.amount_sol ?? 0.05);
  const [slip, setSlip] = useState(w.slippage_bps ?? 300);
  const [enabled, setEnabled] = useState(!!w.enabled);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState([]);
  const [busy, setBusy] = useState("");
  const tokens = w.tokens || [];

  const cfg = async (body) => { try { await api.post("/solana/config", body); toast.success("Saved"); reload(); } catch (e) { err(e); } };
  const search = async () => { try { setHits((await api.get("/solana/tokens/search", { params: { q } })).data); } catch (e) { err(e); } };
  const addToken = (mint) => { if (tokens.includes(mint)) return; cfg({ tokens: [...tokens, mint] }); setHits([]); setQ(""); };
  const swap = async (mint, side) => {
    setBusy(mint + side);
    try { const r = await api.post("/solana/swap", { mint, side }); toast.success(`${side} ${r.data.status} · ${r.data.sig.slice(0, 10)}…`); reload(); }
    catch (e) { err(e); } finally { setBusy(""); }
  };
  const remove = async () => { if (!window.confirm("Remove the Solana wallet from this account?")) return; await api.delete("/solana/wallet"); reload(); };

  return (
    <div className="space-y-5" data-testid="solana-wallet">
      <div className="panel p-3">
        <div className="flex justify-between text-[10px] text-term-muted"><span>WALLET</span><span data-testid="solana-pubkey" className="text-white font-mono">{w.pubkey.slice(0, 6)}…{w.pubkey.slice(-6)}</span></div>
        <div className="flex justify-between text-sm mt-1"><span className="text-term-muted text-[10px]">SOL BALANCE</span><span data-testid="solana-sol-balance" className="text-neon-green">{w.sol_balance != null ? `◎ ${w.sol_balance.toFixed(4)}` : "—"}</span></div>
        {w.error && <p className="text-[10px] text-neon-red mt-1">{w.error}</p>}
      </div>

      <button data-testid="solana-enabled-toggle" onClick={() => { setEnabled(!enabled); cfg({ enabled: !enabled }); }}
        className={`w-full py-2 text-xs tracking-[0.15em] border transition-colors ${enabled ? "border-neon-green text-neon-green bg-neon-green/10" : "border-term-border text-term-muted"}`}>
        {enabled ? "ON · MIRROR BOT SIGNALS INTO THESE TOKENS" : "OFF · SOLANA MIRRORING DISABLED"}
      </button>

      <div className="grid grid-cols-2 gap-4">
        <div><Label>SOL PER BUY</Label><Input data-testid="solana-amount-input" type="number" step="0.01" min="0.001" value={amount} onChange={(e) => setAmount(e.target.value)} /></div>
        <div><Label>SLIPPAGE (BPS)</Label><Input data-testid="solana-slippage-input" type="number" min="10" max="5000" value={slip} onChange={(e) => setSlip(e.target.value)} /></div>
      </div>
      <Btn data-testid="solana-save-button" onClick={() => cfg({ amount_sol: Number(amount), slippage_bps: Number(slip) })}>SAVE</Btn>

      <div>
        <Label>TOKENS TO TRADE ({tokens.length}/5)</Label>
        {tokens.map((m) => {
          const t = w.token_balances?.[m] || {};
          return (
            <div key={m} data-testid={`solana-token-${m}`} className="panel p-2 mb-2 text-xs">
              <div className="flex justify-between items-center">
                <span className="text-white">{t.symbol || m.slice(0, 8)} <span className="text-term-muted text-[9px]">{t.name || ""}</span></span>
                <button data-testid={`solana-token-remove-${m}`} onClick={() => cfg({ tokens: tokens.filter((x) => x !== m) })} className="text-term-muted hover:text-neon-red"><Trash2 size={12} /></button>
              </div>
              <div className="flex justify-between text-[10px] text-term-muted mt-1"><span className="font-mono">{m.slice(0, 10)}…{m.slice(-6)}</span><span>bal {t.ui ?? 0} {t.price ? `· $${Number(t.price).toPrecision(3)}` : ""}</span></div>
              <div className="grid grid-cols-2 gap-2 mt-2">
                <Btn color="neon-green" data-testid={`solana-buy-${m}`} disabled={!!busy} onClick={() => swap(m, "BUY")}>{busy === m + "BUY" ? "SWAPPING…" : `BUY ${amount} SOL`}</Btn>
                <Btn color="neon-red" data-testid={`solana-sell-${m}`} disabled={!!busy || !(t.raw > 0)} onClick={() => swap(m, "SELL")}>{busy === m + "SELL" ? "SWAPPING…" : "SELL ALL"}</Btn>
              </div>
            </div>
          );
        })}
        {tokens.length < 5 && (
          <div className="flex gap-2 items-end">
            <div className="flex-1"><Input data-testid="solana-token-search-input" placeholder="Search symbol or paste mint address" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()} /></div>
            <button data-testid="solana-token-search-button" onClick={search} className="border border-neon-magenta text-neon-magenta px-3 py-2 hover:bg-neon-magenta hover:text-black"><Search size={14} /></button>
          </div>
        )}
        {hits.map((h) => (
          <button key={h.mint} data-testid={`solana-hit-${h.mint}`} onClick={() => addToken(h.mint)}
            className="w-full text-left panel p-2 mt-2 text-xs hover:border-neon-magenta">
            <div className="flex justify-between"><span className="text-white">{h.symbol} <span className="text-term-muted text-[9px]">{h.name}</span></span><span className="text-term-muted text-[9px]">liq ${Math.round(h.liquidity || 0).toLocaleString()}</span></div>
            <div className="font-mono text-[9px] text-term-muted">{h.mint}</div>
          </button>
        ))}
      </div>

      <button data-testid="solana-remove-button" onClick={remove} className="w-full text-[10px] text-term-muted hover:text-neon-red tracking-[0.15em]">REMOVE WALLET</button>
    </div>
  );
}
