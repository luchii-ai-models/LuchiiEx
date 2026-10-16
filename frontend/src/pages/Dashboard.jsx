import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../context/AuthContext";
import Header from "../components/terminal/Header";
import BotStatusCard from "../components/terminal/BotStatusCard";
import CandleChart from "../components/terminal/CandleChart";
import OrderBook from "../components/terminal/OrderBook";
import StreakCard from "../components/terminal/StreakCard";
import SignalScoreCard from "../components/terminal/SignalScoreCard";
import NodeField from "../components/terminal/NodeField";
import PnlChart from "../components/terminal/PnlChart";
import RecentTrades from "../components/terminal/RecentTrades";
import Analytics from "../components/terminal/Analytics";
import ExecutionLog from "../components/terminal/ExecutionLog";
import SettingsModal from "../components/terminal/SettingsModal";
import PendingConfirm from "../components/terminal/PendingConfirm";
import Footer from "../components/terminal/Footer";
import BrokerBadges from "../components/terminal/BrokerBadges";

export default function Dashboard() {
  const { user, logout } = useAuth();
  const [snapshot, setSnapshot] = useState(null);
  const [bot, setBot] = useState(null);
  const [logs, setLogs] = useState([]);
  const [trades, setTrades] = useState([]);
  const [pnl, setPnl] = useState([]);
  const [leaderboard, setLeaderboard] = useState([]);
  const [pending, setPending] = useState(null);
  const [brokers, setBrokers] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState("config");
  const [highlightKill, setHighlightKill] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const timer = useRef(null);

  // Deep-link: /?panel=brokers|settings|guardrails|alerts opens the right settings tab;
  // /?panel=killswitch flashes the kill-switch control on the dashboard.
  useEffect(() => {
    const panel = searchParams.get("panel");
    if (!panel) return;
    if (panel === "killswitch") {
      setHighlightKill(true);
      setTimeout(() => setHighlightKill(false), 4200);
    } else {
      const map = { settings: "config" };
      const validTabs = ["config", "guardrails", "brokers", "alerts", "wallet", "exchange"];
      const tab = map[panel] || panel;
      if (validTabs.includes(tab)) {
        setSettingsTab(tab);
        setSettingsOpen(true);
      }
    }
    searchParams.delete("panel");
    setSearchParams(searchParams, { replace: true });
  }, [searchParams, setSearchParams]);

  const poll = useCallback(async () => {
    try {
      const [s, b, l, t, p, lb, pc, br] = await Promise.all([
        api.get("/market/snapshot"),
        api.get("/bot/state"),
        api.get("/bot/logs"),
        api.get("/bot/trades"),
        api.get("/bot/pnl"),
        api.get("/leaderboard"),
        api.get("/bot/pending"),
        api.get("/brokers/status"),
      ]);
      setSnapshot(s.data); setBot(b.data); setLogs(l.data);
      setTrades(t.data); setPnl(p.data); setLeaderboard(lb.data); setPending(pc.data); setBrokers(br.data);
    } catch (e) { /* transient */ }
  }, []);

  useEffect(() => {
    poll();
    timer.current = setInterval(poll, 3500);
    return () => clearInterval(timer.current);
  }, [poll]);

  return (
    <div className="min-h-screen w-full bg-black text-white flex flex-col lg:h-screen lg:overflow-hidden font-mono">
      <Header bot={bot} snapshot={snapshot} user={user}
        onSettings={() => setSettingsOpen(true)} onLogout={logout} />

      <PendingConfirm pending={pending} refresh={poll} />

      <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-12 gap-2 p-2">
        {/* LEFT */}
        <div className="lg:col-span-3 flex flex-col gap-2 min-h-0">
          <BotStatusCard bot={bot} refresh={poll} highlightKill={highlightKill} />
          <BrokerBadges status={brokers} />
          <div className="min-h-[200px] lg:min-h-0 flex flex-col flex-1">
            <PnlChart points={pnl} bot={bot} />
          </div>
        </div>

        {/* CENTER */}
        <div className="lg:col-span-6 flex flex-col gap-2 min-h-0">
          <CandleChart snapshot={snapshot} bot={bot} />
          <div className="min-h-[260px] lg:min-h-0 flex flex-col flex-1">
            <NodeField logs={logs} />
          </div>
          <ExecutionLog logs={logs} />
        </div>

        {/* RIGHT */}
        <div className="lg:col-span-3 flex flex-col gap-2 min-h-0 lg:overflow-y-auto">
          <StreakCard leaderboard={leaderboard} bot={bot} user={user} />
          <SignalScoreCard bot={bot} />
          <OrderBook snapshot={snapshot} />
          <div className="grid grid-cols-2 gap-2 flex-1 min-h-[220px]">
            <RecentTrades trades={trades} />
            <Analytics trades={trades} bot={bot} />
          </div>
        </div>
      </div>

      {settingsOpen && (
        <SettingsModal bot={bot} initialTab={settingsTab} onClose={() => setSettingsOpen(false)} refresh={poll} />
      )}

      <Footer />
    </div>
  );
}
