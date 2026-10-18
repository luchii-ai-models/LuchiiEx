import { useEffect, useState } from "react";
import { Settings, LogOut, Download, ShieldCheck } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { useInstallPrompt } from "../../hooks/useInstallPrompt";
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
  const nav = useNavigate();
  const [clock, setClock] = useState("");
  const { canInstall, installed, install, isIOS } = useInstallPrompt();

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
            <span className="truncate text-base sm:text-lg">LUCHII <span className="text-white">EX</span></span>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3 sm:gap-6">
        <div className="hidden sm:block"><Stat label="AVAILABLE FUNDS" value={bot?.mode === "live" && bot?.live_equity != null ? `$${Number(bot.live_equity).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—"} cls="text-white" /></div>
        <Stat label="BTC/USD" value={price ? `$${price.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : "—"} cls="text-neon-magenta text-glow-magenta" />
        <div className="hidden md:block"><Stat label="ETH/USD" value={snapshot?.eth ? `$${snapshot.eth.toLocaleString()}` : "—"} cls="text-neon-cyan" /></div>
        <div className="hidden lg:block"><Stat label="SOL/USD" value={snapshot?.sol ? `$${snapshot.sol.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : "—"} cls="text-white" /></div>
        <span className="tnum text-sm text-term-muted hidden lg:inline">{clock}</span>
        {canInstall && !installed && (
          <button data-testid="install-app-button" onClick={handleInstall} title="Install app"
            className="text-neon-cyan hover:text-glow-cyan transition-colors"><Download size={16} /></button>
        )}
        <button data-testid="open-risk-button" onClick={() => nav("/risk")} title="Risk & promotion pipeline"
          className="text-term-muted hover:text-neon-green transition-colors"><ShieldCheck size={16} /></button>
        <button data-testid="open-settings-button" onClick={onSettings}
          className="text-term-muted hover:text-neon-magenta transition-colors"><Settings size={16} /></button>
        <button data-testid="logout-button" onClick={onLogout}
          className="text-term-muted hover:text-neon-red transition-colors"><LogOut size={16} /></button>
      </div>
    </header>
  );
}
