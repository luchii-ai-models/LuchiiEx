import { useEffect, useState } from "react";
import { ArrowDownToLine, ArrowUpFromLine, ArrowLeftRight, History } from "lucide-react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../../lib/api";

const err = (e) => toast.error(formatApiErrorDetail(e.response?.data?.detail));
const field = "w-full bg-transparent border-b border-neon-magenta/60 focus:border-neon-magenta outline-none py-2 text-white text-sm";
const btn = (c) => `border border-${c} text-${c} hover:bg-${c} hover:text-black transition-colors py-2 text-[10px] tracking-[0.12em] disabled:opacity-30`;
const COINS = ["BTC", "USDT", "USDC", "ETH"];

function Section({ icon: Icon, title, children }) {
  return (
    <div className="border-t border-term-border pt-2 space-y-2">
      <div className="flex items-center gap-2 panel-label text-[9px]"><Icon size={11} /> {title}</div>
      {children}
    </div>
  );
}

function Select({ value, onChange, options, testid }) {
  return (
    <select data-testid={testid} value={value} onChange={(e) => onChange(e.target.value)}
      className="w-full bg-term-surface border border-term-border focus:border-neon-magenta outline-none py-1.5 px-2 text-white text-xs">
      {options.map(([v, l]) => <option key={v} value={v} className="bg-black">{l}</option>)}
    </select>
  );
}

function useNetworks(coin) {
  const [nets, setNets] = useState([]);
  useEffect(() => { api.get(`/wallet/real/networks?currency=${coin}`).then((r) => setNets(r.data)).catch(() => setNets([])); }, [coin]);
  return nets;
}

function Balances({ data }) {
  return (
    <div data-testid="real-balances" className="text-[10px] tnum max-h-32 overflow-y-auto space-y-1">
      {Object.entries(data.accounts || { trading: data.balances }).map(([acc, b]) => (
        <div key={acc}>
          <div className="text-term-muted uppercase">{acc}</div>
          {Object.keys(b).length === 0 && <div className="text-term-muted">empty</div>}
          {Object.entries(b).map(([k, v]) => <div key={k} className="flex justify-between"><span>{k}</span><span className="text-white">{v}</span></div>)}
        </div>
      ))}
    </div>
  );
}

function Deposit() {
  const [coin, setCoin] = useState("BTC");
  const [net, setNet] = useState("");
  const [addr, setAddr] = useState(null);
  const nets = useNetworks(coin);
  const load = async () => {
    try { const { data } = await api.get(`/wallet/real/deposit-address?currency=${coin}${net ? `&network=${net}` : ""}`); setAddr(data); }
    catch (e) { err(e); }
  };
  return (
    <Section icon={ArrowDownToLine} title="DEPOSIT">
      <div className="grid grid-cols-3 gap-2">
        <Select testid="deposit-coin-select" value={coin} onChange={(v) => { setCoin(v); setNet(""); setAddr(null); }} options={COINS.map((c) => [c, c])} />
        <Select testid="deposit-network-select" value={net} onChange={setNet} options={[["", "default network"], ...nets.filter((n) => n.deposit).map((n) => [n.id, n.id])]} />
        <button data-testid="real-address-button" onClick={load} className={btn("neon-magenta")}>GET ADDRESS</button>
      </div>
      {addr && (
        <div data-testid="deposit-address" className="text-[10px] break-all border border-neon-magenta/30 bg-neon-magenta/5 p-2">
          <span className="text-term-muted">{addr.currency} deposit address{addr.network ? ` · ${addr.network}` : ""}:</span><br />
          <span className="text-neon-magenta">{addr.address}</span>
          {addr.tag && <div className="text-neon-cyan mt-1">tag/memo: {addr.tag}</div>}
          <button onClick={() => { navigator.clipboard?.writeText(addr.address); toast.success("Address copied"); }} className="block mt-1 text-neon-cyan underline">copy</button>
        </div>
      )}
    </Section>
  );
}

function Transfer({ onDone }) {
  const [t, setT] = useState({ currency: "USDT", amount: "", direction: "to_trading" });
  const go = async () => {
    try { const { data } = await api.post("/wallet/real/transfer", { ...t, amount: Number(t.amount) }); toast.success(`Moved ${t.amount} ${t.currency}: ${data.from} → ${data.to}`); setT({ ...t, amount: "" }); onDone(); }
    catch (e) { err(e); }
  };
  return (
    <Section icon={ArrowLeftRight} title="TRANSFER · FUNDING ⇄ TRADING">
      <div className="grid grid-cols-3 gap-2">
        <Select testid="transfer-coin-select" value={t.currency} onChange={(v) => setT({ ...t, currency: v })} options={COINS.map((c) => [c, c])} />
        <Select testid="transfer-direction-select" value={t.direction} onChange={(v) => setT({ ...t, direction: v })} options={[["to_trading", "funding → trading"], ["to_funding", "trading → funding"]]} />
        <input data-testid="transfer-amount-input" type="number" placeholder="amount" value={t.amount} onChange={(e) => setT({ ...t, amount: e.target.value })} className={field} />
      </div>
      <button data-testid="transfer-button" onClick={go} disabled={!t.amount} className={`w-full ${btn("neon-cyan")}`}>TRANSFER</button>
      <p className="text-[9px] text-term-muted">Deposits land in the funding account on OKX and similar exchanges. Move them to trading so the bot can use them.</p>
    </Section>
  );
}

function Withdraw({ onDone }) {
  const [wd, setWd] = useState({ currency: "BTC", network: "", amount: "", address: "", tag: "" });
  const nets = useNetworks(wd.currency);
  const sel = nets.find((n) => n.id === wd.network);
  const go = async () => {
    try {
      const { data } = await api.post("/wallet/real/withdraw", { ...wd, amount: Number(wd.amount), network: wd.network || null, tag: wd.tag || null });
      toast.success(`Withdrawal submitted #${data.id || ""}`); setWd({ ...wd, amount: "", address: "", tag: "" }); onDone();
    } catch (e) { err(e); }
  };
  return (
    <Section icon={ArrowUpFromLine} title="WITHDRAW">
      <div className="grid grid-cols-2 gap-2">
        <Select testid="withdraw-coin-select" value={wd.currency} onChange={(v) => setWd({ ...wd, currency: v, network: "" })} options={COINS.map((c) => [c, c])} />
        <Select testid="withdraw-network-select" value={wd.network} onChange={(v) => setWd({ ...wd, network: v })} options={[["", "default network"], ...nets.filter((n) => n.withdraw).map((n) => [n.id, `${n.id}${n.fee != null ? ` · fee ${n.fee}` : ""}`])]} />
      </div>
      <input data-testid="withdraw-amount-input" type="number" placeholder={`amount ${wd.currency}${sel?.min ? ` (min ${sel.min})` : ""}`} value={wd.amount} onChange={(e) => setWd({ ...wd, amount: e.target.value })} className={field} />
      <input data-testid="withdraw-address-input" placeholder="destination address" value={wd.address} onChange={(e) => setWd({ ...wd, address: e.target.value })} className={field} />
      <input data-testid="withdraw-tag-input" placeholder="memo / tag (optional)" value={wd.tag} onChange={(e) => setWd({ ...wd, tag: e.target.value })} className={field} />
      <button data-testid="real-withdraw-button" onClick={go} disabled={!wd.amount || !wd.address} className={`w-full ${btn("neon-red")}`}>⚠ WITHDRAW REAL {wd.currency}</button>
      <p className="text-[9px] text-term-muted">Needs a Withdraw-enabled key. OKX withdraws from the funding account and only to addresses on your OKX withdrawal allowlist.</p>
    </Section>
  );
}

function HistoryList({ rows }) {
  if (!rows) return null;
  return (
    <div data-testid="wallet-history" className="text-[10px] tnum max-h-28 overflow-y-auto">
      {rows.length === 0 && <span className="text-term-muted">No deposits or withdrawals yet.</span>}
      {rows.map((r, i) => (
        <div key={i} className="flex justify-between gap-2">
          <span className={r.type === "deposit" ? "text-neon-green" : "text-neon-red"}>{r.type === "deposit" ? "IN" : "OUT"} {r.amount} {r.currency}</span>
          <span className="text-term-muted">{r.status} · {r.ts ? new Date(r.ts).toLocaleString() : ""}</span>
        </div>
      ))}
    </div>
  );
}

export default function RealWallet({ refresh, connected }) {
  const [real, setReal] = useState(null);
  const [hist, setHist] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = async () => {
    setBusy(true);
    try {
      const [b, h] = await Promise.all([api.get("/wallet/real/balance"), api.get("/wallet/real/history")]);
      setReal(b.data); setHist(h.data); refresh?.();
    } catch (e) { err(e); } finally { setBusy(false); }
  };
  useEffect(() => { if (connected) load(); }, [connected]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="border border-term-border p-3 space-y-3">
      <div className="flex items-center justify-between">
        <span className="panel-label">REAL WALLET (EXCHANGE)</span>
        {connected && <button data-testid="real-balance-button" onClick={load} disabled={busy} className={`px-3 ${btn("neon-cyan")}`}>{busy ? "SYNCING…" : "SYNC"}</button>}
      </div>
      {!connected ? (
        <p className="text-[10px] text-term-muted">Connect an exchange wallet below to view real balances, deposit address, and withdraw.</p>
      ) : (
        <>
          {real && <Balances data={real} />}
          <Deposit />
          <Transfer onDone={load} />
          <Withdraw onDone={load} />
          <Section icon={History} title="HISTORY"><HistoryList rows={hist} /></Section>
        </>
      )}
    </div>
  );
}
