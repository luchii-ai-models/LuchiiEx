import { useEffect, useState } from "react";
import { X, Link2, AlertTriangle, Unplug, Bell, Shield, Wallet, Sliders, Send, Server, Radio } from "lucide-react";
import api, { formatApiErrorDetail } from "../../lib/api";
import WalletConnect from "./WalletConnect";
import RealWallet from "./RealWallet";
import ExchangeTools from "./ExchangeTools";
import { toast } from "sonner";

const TABS = [
  ["config", "CONFIG", Sliders],
  ["guardrails", "GUARDRAILS", Shield],
  ["brokers", "BROKERS", Server],
  ["alerts", "ALERTS", Bell],
  ["wallet", "WALLET", Wallet],
  ["exchange", "EXCHANGE", Link2],
];

export default function SettingsModal({ bot, onClose, refresh, initialTab = "config" }) {
  const [tab, setTab] = useState(initialTab);
  const [preset, setPreset] = useState(null);
  const connected = bot?.exchange_connected;
  const goExchange = (id) => { setPreset(id); setTab("exchange"); };

  return (
    <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4" data-testid="settings-modal">
      <div className="panel panel-glow w-full max-w-lg max-h-[92vh] overflow-hidden flex flex-col">
        <div className="flex items-center justify-between px-4 py-3 border-b border-term-border">
          <span className="text-sm tracking-[0.2em] text-neon-magenta text-glow-magenta">◈ ENGINE SETTINGS</span>
          <button data-testid="close-settings-button" onClick={onClose} className="text-term-muted hover:text-neon-red"><X size={16} /></button>
        </div>

        <div className="px-3 py-2 border-b border-term-border">
          <select data-testid="settings-tab-select" value={tab} onChange={(e) => setTab(e.target.value)}
            className="w-full bg-term-surface border border-term-border text-neon-magenta text-xs tracking-[0.15em] px-3 py-2 outline-none focus:border-neon-magenta">
            {TABS.map(([id, label]) => (
              <option key={id} value={id} className="bg-term-surface text-white">{label}</option>
            ))}
          </select>
        </div>

        <div className="p-4 overflow-y-auto">
          {tab === "config" && <ConfigTab bot={bot} refresh={refresh} connected={connected} />}
          {tab === "guardrails" && <GuardrailsTab bot={bot} refresh={refresh} />}
          {tab === "brokers" && <BrokersTab refresh={refresh} />}
          {tab === "alerts" && <AlertsTab bot={bot} refresh={refresh} />}
          {tab === "wallet" && <WalletTab bot={bot} refresh={refresh} connected={connected} goExchange={goExchange} />}
          {tab === "exchange" && <ExchangeTab bot={bot} refresh={refresh} connected={connected} preset={preset} />}
        </div>
      </div>
    </div>
  );
}

const Label = ({ children }) => <div className="panel-label mb-2">{children}</div>;
const Input = (props) => (
  <input {...props} className="w-full bg-transparent border-b border-neon-magenta/60 focus:border-neon-magenta outline-none py-2 text-white text-sm" />
);
const PrimaryBtn = ({ children, ...p }) => (
  <button {...p} className="w-full border border-neon-magenta text-neon-magenta hover:bg-neon-magenta hover:text-black transition-colors py-2 text-xs tracking-[0.2em] disabled:opacity-30">{children}</button>
);

function ConfigTab({ bot, refresh, connected }) {
  const [posUsd, setPosUsd] = useState(bot?.position_usd ?? 50);
  const [autoFund, setAutoFund] = useState(bot?.auto_fund ?? true);

  const save = async () => {
    try { await api.post("/bot/config", { position_usd: Number(posUsd), auto_fund: autoFund }); toast.success("Config saved"); refresh(); }
    catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
  };
  return (
    <div className="space-y-5">
      {!connected && <p data-testid="connect-exchange-hint" className="text-[10px] text-term-muted">Connect an exchange (EXCHANGE tab) to start live trading.</p>}
      <div>
        <Label>POSITION SIZE (USD PER TRADE)</Label>
        <Input data-testid="position-size-input" type="number" min="1" value={posUsd} onChange={(e) => setPosUsd(e.target.value)} />
      </div>
      <div>
        <Label>AUTO-FUND FROM FUNDING ACCOUNT</Label>
        <button data-testid="auto-fund-toggle" onClick={() => setAutoFund(!autoFund)}
          className={`w-full py-2 text-xs tracking-[0.15em] border transition-colors ${autoFund ? "border-neon-green text-neon-green bg-neon-green/10" : "border-term-border text-term-muted"}`}>
          {autoFund ? "ON · MOVE DEPOSITS TO TRADING BEFORE EACH TRADE" : "OFF"}
        </button>
        <p className="text-[9px] text-term-muted mt-1">On OKX, Binance, Bybit, KuCoin, Bitget, Gate and MEXC, deposits land in the funding account. With this on, the bot moves them to trading before each order.</p>
      </div>
      <PrimaryBtn data-testid="save-config-button" onClick={save}>SAVE CONFIG</PrimaryBtn>
    </div>
  );
}

function GuardrailsTab({ bot, refresh }) {
  const [maxOrder, setMaxOrder] = useState(bot?.max_order_usd ?? 100);
  const [dll, setDll] = useState(bot?.daily_loss_limit ?? 200);
  const [manual, setManual] = useState(!!bot?.manual_confirm);

  const save = async () => {
    try {
      await api.post("/bot/config", { max_order_usd: Number(maxOrder), daily_loss_limit: Number(dll), manual_confirm: manual });
      toast.success("Guardrails saved"); refresh();
    } catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
  };
  return (
    <div className="space-y-5">
      <div>
        <Label>MAX ORDER SIZE (USD)</Label>
        <Input data-testid="max-order-input" type="number" min="1" value={maxOrder} onChange={(e) => setMaxOrder(e.target.value)} />
        <p className="text-[10px] text-term-muted mt-1">Caps how much a single order can spend, even if position size is higher.</p>
      </div>
      <div>
        <Label>DAILY LOSS LIMIT (USD)</Label>
        <Input data-testid="daily-loss-input" type="number" min="0" value={dll} onChange={(e) => setDll(e.target.value)} />
        <p className="text-[10px] text-term-muted mt-1">If today's realized loss exceeds this, the kill switch trips automatically.</p>
      </div>
      <div className="flex items-center justify-between border border-term-border p-3">
        <div>
          <div className="text-xs text-white tracking-[0.1em]">MANUAL CONFIRM (LIVE)</div>
          <div className="text-[10px] text-term-muted mt-1">Each live order waits for your approval.</div>
        </div>
        <button data-testid="manual-confirm-toggle" onClick={() => setManual(!manual)}
          className={`w-12 h-6 border relative transition-colors ${manual ? "border-neon-green bg-neon-green/20" : "border-term-border"}`}>
          <span className={`absolute top-0.5 h-4 w-4 transition-all ${manual ? "right-0.5 bg-neon-green" : "left-0.5 bg-term-muted"}`} />
        </button>
      </div>
      <PrimaryBtn data-testid="save-guardrails-button" onClick={save}>SAVE GUARDRAILS</PrimaryBtn>
    </div>
  );
}

function AlertsTab({ bot, refresh }) {
  const [status, setStatus] = useState(null);
  const [chatId, setChatId] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api.get("/alerts/status").then((r) => setStatus(r.data)).catch(() => {});
  useEffect(() => { load(); }, []);

  const link = async () => {
    setBusy(true);
    try { const { data } = await api.post("/alerts/link"); toast.success(`Linked to ${data.name}`); load(); refresh(); }
    catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };
  const setManualId = async () => {
    try { await api.post("/alerts/set", { chat_id: chatId }); toast.success("Chat ID saved"); setChatId(""); load(); refresh(); }
    catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
  };
  const test = async () => {
    try { await api.post("/alerts/test"); toast.success("Test alert sent"); }
    catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
  };
  const toggle = async () => {
    try { await api.post("/alerts/config", { alerts_enabled: !status?.enabled }); load(); refresh(); }
    catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
  };

  return (
    <div className="space-y-4">
      <div className="text-[11px] text-term-muted leading-relaxed">
        Get a Telegram ping on every autonomous trade, confirmations, and kill-switch events.
      </div>
      <div className="border border-term-border p-3 space-y-2">
        <div className="text-xs text-white">HOW TO LINK</div>
        <ol className="text-[10px] text-term-muted list-decimal ml-4 space-y-1">
          <li>Open Telegram and send any message to your bot.</li>
          <li>Press <span className="text-neon-cyan">LINK TELEGRAM</span> below — we auto-detect your chat.</li>
        </ol>
      </div>

      {status?.chat_id ? (
        <div className="flex items-center justify-between border border-neon-green/40 bg-neon-green/5 p-3">
          <div className="text-xs">
            <div className="text-neon-green tracking-[0.15em]">● LINKED</div>
            <div className="text-term-muted mt-1 tnum">chat {String(status.chat_id)}</div>
          </div>
          <button data-testid="alerts-toggle-button" onClick={toggle}
            className={`px-3 py-1.5 text-[10px] border tracking-[0.15em] ${status.enabled ? "border-neon-green text-neon-green" : "border-term-muted text-term-muted"}`}>
            {status.enabled ? "ENABLED" : "DISABLED"}
          </button>
        </div>
      ) : (
        <div className="text-[10px] text-term-muted">Not linked yet.</div>
      )}

      <button data-testid="alerts-link-button" onClick={link} disabled={busy}
        className="w-full border border-neon-cyan text-neon-cyan hover:bg-neon-cyan hover:text-black transition-colors py-2 text-xs tracking-[0.2em] flex items-center justify-center gap-2 disabled:opacity-40">
        <Send size={13} /> {busy ? "DETECTING…" : "LINK TELEGRAM"}
      </button>

      <div className="grid grid-cols-2 gap-2">
        <Input data-testid="chat-id-input" placeholder="or paste chat ID" value={chatId} onChange={(e) => setChatId(e.target.value)} />
        <button data-testid="alerts-set-button" onClick={setManualId} disabled={!chatId}
          className="border border-term-border text-term-muted hover:border-neon-magenta hover:text-neon-magenta transition-colors py-2 text-xs tracking-[0.15em] disabled:opacity-30">SET</button>
      </div>

      {status?.chat_id && (
        <button data-testid="alerts-test-button" onClick={test}
          className="w-full border border-neon-magenta text-neon-magenta hover:bg-neon-magenta hover:text-black transition-colors py-2 text-xs tracking-[0.2em]">SEND TEST ALERT</button>
      )}
    </div>
  );
}

function WalletTab({ bot, refresh, connected, goExchange }) {
  return (
    <div className="space-y-5">
      <RealWallet bot={bot} refresh={refresh} connected={connected} />
      <WalletConnect connected={connected} goExchange={goExchange} />
    </div>
  );
}

const KEY_HELP = {
  coinbase: "coinbase.com → Settings → API → New API key (CDP). Enable View + Trade. API KEY = key name (organizations/…/apiKeys/…). SECRET = the full private key incl. -----BEGIN/END----- lines.",
  kraken: "kraken.com → Settings → API → Create key. Enable Query Funds, Query/Create & Modify Orders (+ Withdraw Funds for withdrawals).",
  okx: "okx.com → Profile → API → Create V5 key with Read + Trade (+ Withdraw) permission, Trusted IP off. Enter the passphrase you set when creating it. Global, US and EEA accounts are detected automatically.",
  binanceus: "binance.us → Profile → API Management → Create API. Enable Spot Trading; leave IP restriction off (or whitelist our server).",
  binance: "binance.com → API Management. Note: binance.com blocks some server regions (error 451). Use Binance.US if you are in the US.",
  cryptocom: "crypto.com/exchange → Settings → API Keys. Enable Trade. (The Crypto.com App wallet has no trading API; use the Exchange.)",
  kucoin: "kucoin.com → API Management → Create API with General + Trade. Enter the API passphrase.",
  bitget: "bitget.com → API Management → Create API with Trade permission. Enter the API passphrase.",
};

function ExchangeTab({ bot, refresh, connected, preset }) {
  const [exchanges, setExchanges] = useState([]);
  const [exchange, setExchange] = useState(preset || "kraken");
  const [apiKey, setApiKey] = useState("");
  const [secret, setSecret] = useState("");
  const [password, setPassword] = useState("");
  const [test, setTest] = useState(null);
  const [busy, setBusy] = useState(false);
  const needsPass = ["okx", "kucoin", "bitget"].includes(exchange);

  useEffect(() => { api.get("/exchange/list").then((r) => { setExchanges(r.data); if (!preset && r.data[0]) setExchange(r.data[2] || r.data[0]); }).catch(() => {}); }, [preset]);

  const connect = async () => {
    setBusy(true);
    try {
      const { data } = await api.post("/exchange/connect", { exchange, api_key: apiKey, secret, password });
      toast.success(`Connected to ${exchange}${data.hostname ? ` (${data.hostname})` : ""} · LIVE`);
      (data.warnings || []).forEach((w) => toast.warning(w, { duration: 12000 }));
      setApiKey(""); setSecret(""); setPassword(""); refresh();
    }
    catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };
  const disconnect = async () => { await api.post("/exchange/disconnect"); toast.success("Disconnected"); refresh(); };

  const testOrder = async () => {
    setBusy(true);
    try {
      const { data } = await api.post("/exchange/test-order");
      setTest(data);
      toast.success(`Live test filled: BUY #${data.buy.id} · SELL #${data.sell.id}`);
      refresh();
    } catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  if (connected)
    return (
      <div className="space-y-3">
      <div className="flex items-center justify-between border border-neon-green/40 bg-neon-green/5 p-3">
        <div className="text-xs">
          <div className="text-neon-green tracking-[0.15em]">● CONNECTED</div>
          <div className="text-term-muted mt-1 uppercase">{bot?.exchange}</div>
        </div>
        <button data-testid="disconnect-exchange-button" onClick={disconnect}
          className="border border-neon-red text-neon-red hover:bg-neon-red hover:text-black transition-colors px-3 py-1.5 text-xs flex items-center gap-1"><Unplug size={12} /> DISCONNECT</button>
      </div>
      <ExchangeTools refresh={refresh} />
      <button data-testid="live-test-order-button" onClick={testOrder} disabled={busy}
        className="w-full border border-neon-magenta text-neon-magenta hover:bg-neon-magenta hover:text-black transition-colors py-2 text-xs tracking-[0.2em] disabled:opacity-30">
        {busy ? "EXECUTING LIVE ORDER…" : "FIRE LIVE TEST ORDER (MIN SIZE BUY → SELL)"}
      </button>
      {test && (
        <div data-testid="live-test-order-result" className="text-[10px] tnum border border-neon-green/40 bg-neon-green/5 p-2 space-y-1">
          <div className="text-neon-green">BUY #{test.buy.id} · {test.buy.status} · {test.buy.filled} BTC @ {test.buy.average ?? test.price}</div>
          <div className="text-neon-red">SELL #{test.sell.id} · {test.sell.status} · {test.sell.filled} BTC @ {test.sell.average ?? test.price}</div>
          <div className="text-term-muted">{test.symbol} · ${test.usd}</div>
        </div>
      )}
      </div>
    );

  return (
    <div className="space-y-3">
      <div>
        <span className="panel-label text-[9px]">EXCHANGE</span>
        <select data-testid="exchange-select" value={exchange} onChange={(e) => setExchange(e.target.value)}
          className="mt-1 w-full bg-term-surface border border-term-border focus:border-neon-magenta outline-none py-2 px-2 text-white text-sm">
          {exchanges.map((x) => <option key={x} value={x} className="bg-black">{x}</option>)}
        </select>
      </div>
      <div><span className="panel-label text-[9px]">API KEY</span><Input data-testid="api-key-input" value={apiKey} onChange={(e) => setApiKey(e.target.value)} /></div>
      <div><span className="panel-label text-[9px]">API SECRET</span>
        <textarea data-testid="api-secret-input" rows={exchange === "coinbase" ? 5 : 2} value={secret} onChange={(e) => setSecret(e.target.value)}
          placeholder={exchange === "coinbase" ? "-----BEGIN EC PRIVATE KEY-----\n…\n-----END EC PRIVATE KEY-----" : ""}
          className="w-full bg-transparent border-b border-neon-magenta/60 focus:border-neon-magenta outline-none py-2 text-white text-sm font-mono resize-none" style={{ WebkitTextSecurity: exchange === "coinbase" ? "none" : "disc" }} /></div>
      {KEY_HELP[exchange] && <p data-testid="exchange-key-help" className="text-[9px] text-neon-cyan/80 leading-relaxed">{KEY_HELP[exchange]}</p>}
      {needsPass && <div><span className="panel-label text-[9px]">API PASSPHRASE</span><Input data-testid="api-passphrase-input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></div>}
      <button data-testid="connect-exchange-button" onClick={connect} disabled={busy || !apiKey || !secret || (needsPass && !password)}
        className="w-full border border-neon-green text-neon-green hover:bg-neon-green hover:text-black transition-colors py-2 text-xs tracking-[0.2em] disabled:opacity-30">
        {busy ? "VERIFYING…" : "CONNECT & VERIFY"}
      </button>
      <p className="text-[10px] text-term-muted">Keys are encrypted at rest. We verify by reading your balance. Grant trade + withdrawal permissions for live execution & withdrawals.</p>
    </div>
  );
}


function BrokersTab({ refresh }) {
  const [status, setStatus] = useState(null);
  const [mt, setMt] = useState({ token: "", login: "", password: "", server: "", platform: "mt5", symbols: "BTCUSD", volume: 0.01 });
  const [po, setPo] = useState({ ssid: "", is_demo: false, asset: "BTCUSD_otc", amount: 1, duration: 60 });
  const [busy, setBusy] = useState("");
  const [info, setInfo] = useState(null);

  const load = () => api.get("/brokers/status").then((r) => setStatus(r.data)).catch(() => {});
  useEffect(() => { load(); }, []);

  const mtConnected = status?.metaapi?.connected;
  const poConnected = status?.pocketoption?.connected;

  const connectMt = async () => {
    setBusy("mt");
    try { const { data } = await api.post("/brokers/metaapi/connect", mt); toast.success("MetaTrader connecting…", { description: data.note }); load(); refresh(); }
    catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
    finally { setBusy(""); }
  };
  const mtInfo = async () => {
    setBusy("info");
    try { const { data } = await api.get("/brokers/metaapi/info"); setInfo(data); toast.success(`Balance ${data.balance} ${data.currency || ""}`); }
    catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
    finally { setBusy(""); }
  };
  const mtDisc = async () => { await api.post("/brokers/metaapi/disconnect"); toast.success("MetaTrader disconnected"); setInfo(null); load(); refresh(); };

  const connectPo = async () => {
    setBusy("po");
    try { const { data } = await api.post("/brokers/pocketoption/connect", po); toast.success("PocketOption connected", { description: `Balance ${data.balance ?? "?"}` }); load(); refresh(); }
    catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
    finally { setBusy(""); }
  };
  const poDisc = async () => { await api.post("/brokers/pocketoption/disconnect"); toast.success("PocketOption disconnected"); load(); refresh(); };

  const mirrorTest = async () => {
    try { await api.post("/brokers/mirror-test"); toast.success("Mirror BUY dispatched — check execution log"); }
    catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
  };

  return (
    <div className="space-y-5">
      <div className="text-[11px] text-term-muted leading-relaxed">
        Connect brokers to mirror Luchii's BTC BUY/SELL signals to every account at once (alongside your crypto exchange).
      </div>

      {/* MetaTrader */}
      <div className="border border-term-border p-3 space-y-3">
        <div className="flex items-center gap-2 panel-label"><Server size={12} /> METATRADER 4 / 5 · METAAPI</div>
        {mtConnected ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between border border-neon-green/40 bg-neon-green/5 p-2">
              <div className="text-xs">
                <span className="text-neon-green tracking-[0.15em]">● CONNECTED</span>
                <div className="text-term-muted mt-1 uppercase tnum">{status.metaapi.platform} · {status.metaapi.server}</div>
                <div className="text-term-muted text-[10px]">{status.metaapi.symbols} · {status.metaapi.volume} lots</div>
              </div>
              <div className="flex flex-col gap-1">
                <button data-testid="metaapi-info-button" onClick={mtInfo} disabled={busy === "info"}
                  className="border border-neon-cyan text-neon-cyan hover:bg-neon-cyan hover:text-black transition-colors px-2 py-1 text-[10px]">{busy === "info" ? "…" : "INFO"}</button>
                <button data-testid="metaapi-disconnect-button" onClick={mtDisc}
                  className="border border-neon-red text-neon-red hover:bg-neon-red hover:text-black transition-colors px-2 py-1 text-[10px]">DROP</button>
              </div>
            </div>
            {info && <div className="text-[10px] tnum text-white">Balance {info.balance} · Equity {info.equity} {info.currency}</div>}
          </div>
        ) : (
          <div className="space-y-2">
            <div><span className="panel-label text-[9px]">METAAPI API TOKEN</span><Input data-testid="mt-token-input" type="password" value={mt.token} onChange={(e) => setMt({ ...mt, token: e.target.value })} /></div>
            <div className="grid grid-cols-2 gap-2">
              <div><span className="panel-label text-[9px]">MT LOGIN</span><Input data-testid="mt-login-input" value={mt.login} onChange={(e) => setMt({ ...mt, login: e.target.value })} /></div>
              <div><span className="panel-label text-[9px]">PASSWORD</span><Input data-testid="mt-password-input" type="password" value={mt.password} onChange={(e) => setMt({ ...mt, password: e.target.value })} /></div>
            </div>
            <div><span className="panel-label text-[9px]">SERVER</span><Input data-testid="mt-server-input" placeholder="e.g. ICMarkets-Demo" value={mt.server} onChange={(e) => setMt({ ...mt, server: e.target.value })} /></div>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <span className="panel-label text-[9px]">PLATFORM</span>
                <select data-testid="mt-platform-select" value={mt.platform} onChange={(e) => setMt({ ...mt, platform: e.target.value })}
                  className="mt-1 w-full bg-term-surface border border-term-border focus:border-neon-magenta outline-none py-2 px-1 text-white text-xs">
                  <option value="mt5" className="bg-black">MT5</option><option value="mt4" className="bg-black">MT4</option>
                </select>
              </div>
              <div><span className="panel-label text-[9px]">SYMBOLS</span><Input data-testid="mt-symbols-input" value={mt.symbols} onChange={(e) => setMt({ ...mt, symbols: e.target.value })} /></div>
              <div><span className="panel-label text-[9px]">LOTS</span><Input data-testid="mt-volume-input" type="number" step="0.01" value={mt.volume} onChange={(e) => setMt({ ...mt, volume: Number(e.target.value) })} /></div>
            </div>
            <button data-testid="metaapi-connect-button" onClick={connectMt} disabled={busy === "mt" || !mt.token || !mt.login || !mt.server}
              className="w-full border border-neon-green text-neon-green hover:bg-neon-green hover:text-black transition-colors py-2 text-xs tracking-[0.15em] disabled:opacity-30">
              {busy === "mt" ? "PROVISIONING…" : "CONNECT METATRADER"}
            </button>
            <p className="text-[9px] text-term-muted">Use an <span className="text-neon-cyan">API access token</span> from app.metaapi.cloud (not an account token). Symbols comma-separated; check your broker's exact BTCUSD symbol.</p>
          </div>
        )}
      </div>

      {/* PocketOption */}
      <div className="border border-neon-amber/40 p-3 space-y-3">
        <div className="flex items-center gap-2 panel-label text-neon-amber"><Radio size={12} /> POCKETOPTION · EXPERIMENTAL</div>
        <div className="flex items-start gap-2 text-[10px] text-neon-amber border border-neon-amber/40 bg-neon-amber/5 p-2">
          <AlertTriangle size={13} className="shrink-0 mt-0.5" />
          Unofficial / reverse-engineered. Against PocketOption ToS, may break or flag your account, and often fails from cloud IPs. Use at your own risk.
        </div>
        {poConnected ? (
          <div className="flex items-center justify-between border border-neon-green/40 bg-neon-green/5 p-2">
            <div className="text-xs">
              <span className="text-neon-green tracking-[0.15em]">● CONNECTED</span>
              <div className="text-term-muted mt-1 tnum">{status.pocketoption.asset} · ${status.pocketoption.amount} · {status.pocketoption.duration}s · {status.pocketoption.is_demo ? "DEMO" : "REAL"}</div>
            </div>
            <button data-testid="po-disconnect-button" onClick={poDisc}
              className="border border-neon-red text-neon-red hover:bg-neon-red hover:text-black transition-colors px-2 py-1 text-[10px]">DROP</button>
          </div>
        ) : (
          <div className="space-y-2">
            <div><span className="panel-label text-[9px]">SESSION SSID</span><Input data-testid="po-ssid-input" type="password" value={po.ssid} onChange={(e) => setPo({ ...po, ssid: e.target.value })} /></div>
            <div className="grid grid-cols-3 gap-2">
              <div><span className="panel-label text-[9px]">ASSET</span><Input data-testid="po-asset-input" value={po.asset} onChange={(e) => setPo({ ...po, asset: e.target.value })} /></div>
              <div><span className="panel-label text-[9px]">AMOUNT $</span><Input data-testid="po-amount-input" type="number" value={po.amount} onChange={(e) => setPo({ ...po, amount: Number(e.target.value) })} /></div>
              <div><span className="panel-label text-[9px]">EXPIRY s</span><Input data-testid="po-duration-input" type="number" value={po.duration} onChange={(e) => setPo({ ...po, duration: Number(e.target.value) })} /></div>
            </div>
            <div className="flex items-center justify-between border border-term-border p-2">
              <span className="text-[10px] text-white">DEMO ACCOUNT</span>
              <button data-testid="po-demo-toggle" onClick={() => setPo({ ...po, is_demo: !po.is_demo })}
                className={`w-12 h-6 border relative transition-colors ${po.is_demo ? "border-neon-green bg-neon-green/20" : "border-neon-red bg-neon-red/10"}`}>
                <span className={`absolute top-0.5 h-4 w-4 transition-all ${po.is_demo ? "right-0.5 bg-neon-green" : "left-0.5 bg-neon-red"}`} />
              </button>
            </div>
            <button data-testid="po-connect-button" onClick={connectPo} disabled={busy === "po" || !po.ssid}
              className="w-full border border-neon-amber text-neon-amber hover:bg-neon-amber hover:text-black transition-colors py-2 text-xs tracking-[0.15em] disabled:opacity-30">
              {busy === "po" ? "CONNECTING…" : "CONNECT POCKETOPTION"}
            </button>
            <p className="text-[9px] text-term-muted">Get the SSID from your browser's PocketOption websocket (auth message) while logged in.</p>
          </div>
        )}
      </div>

      {(mtConnected || poConnected) && (
        <button data-testid="mirror-test-button" onClick={mirrorTest}
          className="w-full border border-neon-magenta text-neon-magenta hover:bg-neon-magenta hover:text-black transition-colors py-2 text-xs tracking-[0.2em]">
          🔁 FIRE TEST MIRROR (BUY)
        </button>
      )}
    </div>
  );
}
