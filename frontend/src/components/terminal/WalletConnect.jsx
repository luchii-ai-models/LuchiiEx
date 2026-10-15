import { useEffect, useState } from "react";
import { Wallet, Link2, Banknote, Trash2 } from "lucide-react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../../lib/api";

const EXCHANGE_WALLETS = [
  ["coinbase", "COINBASE"], ["kraken", "KRAKEN"], ["okx", "OKX"], ["binanceus", "BINANCE.US"],
  ["cryptocom", "CRYPTO.COM"], ["binance", "BINANCE"], ["bybit", "BYBIT"], ["kucoin", "KUCOIN"],
];
const PAYMENT_APPS = ["CASH APP", "PAYPAL", "SQUARE"];

async function connectBrowserWallet(kind) {
  if (kind === "okxwallet") {
    const okx = window.okxwallet;
    if (!okx) throw new Error("OKX Wallet extension not found in this browser. Install it or open this page in the OKX app browser.");
    if (okx.bitcoin) {
      const r = await okx.bitcoin.connect();
      return { chain: "btc", address: r.address };
    }
    const [a] = await okx.request({ method: "eth_requestAccounts" });
    return { chain: "evm", address: a };
  }
  const eth = kind === "coinbasewallet"
    ? (window.coinbaseWalletExtension || (window.ethereum?.isCoinbaseWallet ? window.ethereum : null))
    : window.ethereum;
  if (!eth) throw new Error(`${kind === "coinbasewallet" ? "Coinbase Wallet" : "Web3 wallet"} not found in this browser. Install the extension or open this page in the wallet's in-app browser.`);
  const [a] = await eth.request({ method: "eth_requestAccounts" });
  return { chain: "evm", address: a };
}

export default function WalletConnect({ connected, goExchange, depositAddr, loadAddr }) {
  const [wallets, setWallets] = useState([]);
  const [manual, setManual] = useState("");
  const [payApp, setPayApp] = useState(null);

  const load = () => api.get("/wallet/web3").then((r) => setWallets(r.data)).catch(() => {});
  useEffect(() => { load(); }, []);

  const link = async (provider, payload) => {
    try { await api.post("/wallet/web3/link", { provider, ...payload }); toast.success(`${provider} linked`); load(); }
    catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
  };
  const browser = async (kind, label) => {
    try { link(label, await connectBrowserWallet(kind)); } catch (e) { toast.error(e.message || "Wallet connection failed"); }
  };
  const remove = async (address) => { await api.delete(`/wallet/web3/${address}`); load(); };

  const btn = "border border-term-border text-white hover:border-neon-magenta hover:text-neon-magenta transition-colors py-2 text-[10px] tracking-[0.12em]";
  return (
    <div className="space-y-4" data-testid="wallet-connect">
      <div className="border border-term-border p-3 space-y-2">
        <div className="flex items-center gap-2 panel-label"><Link2 size={12} /> EXCHANGE WALLETS · LIVE TRADING</div>
        <div className="grid grid-cols-4 gap-1.5">
          {EXCHANGE_WALLETS.map(([id, label]) => (
            <button key={id} data-testid={`wallet-exchange-${id}`} onClick={() => goExchange(id)} className={btn}>{label}</button>
          ))}
        </div>
        <p className="text-[9px] text-term-muted">Opens the EXCHANGE tab with this provider selected. Paste the API key from your account; the bot trades this wallet's real funds.</p>
      </div>

      <div className="border border-term-border p-3 space-y-2">
        <div className="flex items-center gap-2 panel-label"><Wallet size={12} /> SELF-CUSTODY WALLETS · VIEW + DEPOSIT</div>
        <div className="grid grid-cols-3 gap-1.5">
          <button data-testid="wallet-okxwallet" onClick={() => browser("okxwallet", "OKX Wallet")} className={btn}>OKX WALLET</button>
          <button data-testid="wallet-coinbasewallet" onClick={() => browser("coinbasewallet", "Coinbase Wallet")} className={btn}>COINBASE WALLET</button>
          <button data-testid="wallet-metamask" onClick={() => browser("ethereum", "Web3 Wallet")} className={btn}>METAMASK / WEB3</button>
        </div>
        <div className="grid grid-cols-3 gap-2">
          <input data-testid="wallet-manual-address-input" placeholder="or paste BTC / 0x address" value={manual} onChange={(e) => setManual(e.target.value)}
            className="col-span-2 w-full bg-transparent border-b border-neon-magenta/60 focus:border-neon-magenta outline-none py-2 text-white text-sm" />
          <button data-testid="wallet-manual-link-button" disabled={!manual} className={btn}
            onClick={() => { link("Wallet", { chain: manual.startsWith("0x") ? "evm" : "btc", address: manual.trim() }); setManual(""); }}>LINK</button>
        </div>
        {wallets.map((w) => (
          <div key={w.address} data-testid={`linked-wallet-${w.address}`} className="flex items-center justify-between text-[10px] tnum border-t border-term-border pt-1.5">
            <span className="text-term-muted truncate max-w-[55%]">{w.provider} · {w.address}</span>
            <span className="text-neon-cyan">{w.balance ?? "—"} {w.chain === "btc" ? "BTC" : "ETH"}</span>
            <button data-testid={`unlink-wallet-${w.address}`} onClick={() => remove(w.address)} className="text-term-muted hover:text-neon-red"><Trash2 size={11} /></button>
          </div>
        ))}
        <p className="text-[9px] text-term-muted">Self-custody wallets keep your keys, so the bot can't sign trades from them. Move BTC to a connected exchange wallet to trade it live.</p>
      </div>

      <div className="border border-term-border p-3 space-y-2">
        <div className="flex items-center gap-2 panel-label"><Banknote size={12} /> PAYMENT APPS · FUND YOUR TRADING WALLET</div>
        <div className="grid grid-cols-3 gap-1.5">
          {PAYMENT_APPS.map((p) => (
            <button key={p} data-testid={`wallet-pay-${p.replace(" ", "").toLowerCase()}`} onClick={() => setPayApp(p)}
              className={`${btn} ${payApp === p ? "border-neon-magenta text-neon-magenta" : ""}`}>{p}</button>
          ))}
        </div>
        {payApp && (
          <div data-testid="payment-app-steps" className="text-[10px] text-term-muted space-y-1 border-t border-term-border pt-2">
            <div className="text-white">FUND FROM {payApp}</div>
            <div>{payApp} has no trading API, so it can't be linked for live trading. Send BTC from it to your exchange wallet:</div>
            {!connected ? (
              <div className="text-neon-amber">1. Connect an exchange wallet above first.</div>
            ) : depositAddr ? (
              <div className="break-all">1. In {payApp}: Bitcoin → Send → paste <span className="text-neon-magenta">{depositAddr}</span></div>
            ) : (
              <button data-testid="payment-app-get-address" onClick={loadAddr} className="text-neon-cyan underline">1. Get my BTC deposit address</button>
            )}
            <div>2. Once the deposit arrives on the exchange, press START. The engine trades the new balance.</div>
          </div>
        )}
      </div>
    </div>
  );
}
