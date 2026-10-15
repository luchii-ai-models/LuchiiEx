import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Settings, LogOut, Activity, Download, Sun, Moon, HelpCircle, MessageSquare } from "lucide-react";
import { toast } from "sonner";
import { useInstallPrompt } from "../../hooks/useInstallPrompt";
import { useTheme } from "../../context/ThemeContext";
import Logo from "./Logo";

function Stat({ label, value, cls = "text-white" }) {
  return (
    <div className="flex flex-col items-end leading-none">
      <span className="panel-label text-[9px]">{label}</span>
      <span className={`tnum text-sm font-bold ${cls}`}>{value}</span>
    </div>
  );
}

export default function Header({ bot, snapshot, user, onSettings, onLogout }) {
  const [clock, setClock] = useState("");
  const { canInstall, installed, install, isIOS } = useInstallPrompt();
  const { theme, toggle } = useTheme();
  const navigate = useNavigate();

  useEffect(() => {
    const t = setInterval(() => setClock(new Date().toLocaleTimeString("en-GB")), 1000);
    return () => clearInterval(t);
  }, []);

  const price = snapshot?.price;
  const live = bot?.status === "running";

  const handleInstall = async () => {
    const r = await install();
    if (r === "ios")
      toast("Install on iOS", { description: "Tap the Share icon, then 'Add to Home Screen'." });
    else if (r === "unavailable")
      toast("Already installed or unsupported", { description: "Use your browser menu → Install app." });
  };

  return (
    <header className="shrink-0 border-b border-term-border bg-term-surface px-3 sm:px-4 py-2 flex items-center justify-between gap-2 flex-wrap">
      <div className="flex items-center gap-2 sm:gap-3 min-w-0">
        <div className="shrink-0"><Logo size={36} /></div>
        <div className="leading-none min-w-0">
          <div className="text-xs sm:text-sm font-extrabold tracking-tighter flex items-center gap-1.5 sm:gap-2 flex-wrap">
            <span className="truncate">LUCHII 4.7 · <span className="text-neon-magenta text-glow-magenta">FRASBERG</span> · BTC</span>
            <span className={`px-1.5 py-0.5 text-[9px] tracking-[0.2em] border ${live ? "border-neon-green text-neon-green" : "border-term-muted text-term-muted"}`}>
              {live ? "● LIVE" : "○ IDLE"}
            </span>
            {bot?.mode === "live"
              ? <span className="px-1.5 py-0.5 text-[9px] tracking-[0.2em] border border-neon-red text-neon-red">REAL</span>
              : <span className="px-1.5 py-0.5 text-[9px] tracking-[0.2em] border border-neon-cyan text-neon-cyan">DRY</span>}
          </div>
          <div className="panel-label mt-1 hidden sm:flex items-center gap-1"><Activity size={9} /> COINBASE FEED · LUCHII ENGINE</div>
        </div>
      </div>

      <div className="flex items-center gap-3 sm:gap-6">
        <Stat label="BTC/USD" value={price ? `$${price.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : "—"} cls="text-neon-magenta text-glow-magenta" />
        <div className="hidden md:block"><Stat label="ETH/USD" value={snapshot?.eth ? `$${snapshot.eth.toLocaleString()}` : "—"} cls="text-neon-cyan" /></div>
        <div className="hidden sm:block"><Stat label="TRADES" value={bot ? `${bot.trades_count}` : "—"} /></div>
        <Stat label="WIN%" value={bot ? `${bot.win_rate}%` : "—"} cls="text-neon-green" />
        <span className="tnum text-sm text-term-muted hidden lg:inline">{clock}</span>
        <button data-testid="theme-toggle-button" onClick={toggle} title="Toggle theme"
          className="text-term-muted hover:text-neon-amber transition-colors">
          {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
        </button>
        {canInstall && !installed && (
          <button data-testid="install-app-button" onClick={handleInstall} title="Install app"
            className="text-neon-cyan hover:text-glow-cyan transition-colors"><Download size={16} /></button>
        )}
        <button data-testid="open-chat-button" onClick={() => navigate("/chat")} title="Luchii chat"
          className="text-term-muted hover:text-neon-magenta transition-colors"><MessageSquare size={16} /></button>
        <button data-testid="open-help-button" onClick={() => navigate("/help")} title="Help center"
          className="text-term-muted hover:text-neon-cyan transition-colors"><HelpCircle size={16} /></button>
        <button data-testid="open-settings-button" onClick={onSettings}
          className="text-term-muted hover:text-neon-magenta transition-colors"><Settings size={16} /></button>
        <button data-testid="logout-button" onClick={onLogout}
          className="text-term-muted hover:text-neon-red transition-colors"><LogOut size={16} /></button>
      </div>
    </header>
  );
}
