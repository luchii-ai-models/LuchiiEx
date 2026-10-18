function Badge({ label, data, kind }) {
  // kind: exchange | mt | po
  const connected = data?.connected;
  const warm = data?.warm;
  const m = data?.metrics;
  const state = !connected ? "OFF" : kind === "exchange" ? "ON" : warm ? "WARM" : "COLD";
  const dot =
    state === "OFF" ? "#4b4b52" :
    state === "WARM" || state === "ON" ? "#39ff14" : "#ffb800";
  const ageOk = m && Date.now() - m.ts < 5 * 60 * 1000;

  return (
    <div className="flex items-center justify-between border border-term-border px-2 py-1" data-testid={`broker-badge-${kind}`}>
      <div className="flex items-center gap-1.5 min-w-0">
        <span className="h-2 w-2 rounded-full shrink-0"
          style={{ background: dot, boxShadow: connected ? `0 0 6px ${dot}` : "none" }} />
        <span className="text-[10px] tracking-[0.12em] text-white truncate">{label}</span>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <span className="panel-label text-[8px]">{state}</span>
        {m && ageOk && (
          <span className={`tnum text-[10px] ${m.ok ? "text-neon-green" : "text-neon-red"}`}>
            {m.ok ? "" : "✕ "}{m.last_ms}ms
          </span>
        )}
      </div>
    </div>
  );
}

export default function BrokerBadges({ status }) {
  const s = status || {};
  const any = s.exchange?.connected || s.metaapi?.connected || s.pocketoption?.connected;

  return (
    <div className="panel p-2 shrink-0" data-testid="broker-health-panel">
      <div className="flex items-center justify-between mb-1.5">
        <span className="panel-label">◈ EXCHANGE</span>
        <span className="panel-label text-[8px]">{any ? "ON" : "OFF"}</span>
      </div>
      {!any ? (
        <div className="panel-label text-[9px] py-1">NO EXCHANGE LINKED · SETTINGS → EXCHANGE</div>
      ) : (
        <div className="space-y-1">
          {s.exchange?.connected && <Badge label={`EXCHANGE · ${(s.exchange.name || "").toUpperCase()}`} data={s.exchange} kind="exchange" />}
          {s.metaapi?.connected && <Badge label={`METATRADER · ${(s.metaapi.platform || "").toUpperCase()}`} data={s.metaapi} kind="mt" />}
          {s.pocketoption?.connected && <Badge label={`POCKETOPTION${s.pocketoption.is_demo ? " · DEMO" : ""}`} data={s.pocketoption} kind="po" />}
        </div>
      )}
    </div>
  );
}
