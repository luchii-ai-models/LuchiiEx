import { LineChart, Line, ResponsiveContainer, YAxis, Tooltip } from "recharts";

export default function PnlChart({ points, bot }) {
  const data = (points || []).map((p, i) => ({ i, equity: p.equity }));
  const first = data[0]?.equity ?? 10000;
  const last = data[data.length - 1]?.equity ?? first;
  const up = last >= first;
  const delta = last - first;

  return (
    <div className="panel flex-1 min-h-0 p-2 flex flex-col">
      <div className="flex items-center justify-between">
        <span className="panel-label">◈ PNL · TOTAL</span>
        <span className={`tnum text-sm font-bold ${up ? "text-neon-green text-glow-green" : "text-neon-red"}`}>
          {delta >= 0 ? "+" : ""}${delta.toFixed(2)}
        </span>
      </div>
      <div className="flex-1 min-h-0 mt-1">
        {data.length > 1 ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 6, right: 2, bottom: 0, left: 2 }}>
              <YAxis domain={["dataMin", "dataMax"]} hide />
              <Tooltip
                contentStyle={{ background: "#050505", border: "1px solid #39ff14", borderRadius: 0, fontFamily: "'JetBrains Mono'", fontSize: 11 }}
                labelStyle={{ display: "none" }}
                formatter={(v) => [`$${Number(v).toFixed(2)}`, "EQUITY"]} />
              <Line type="monotone" dataKey="equity" stroke={up ? "#39ff14" : "#ff003c"} strokeWidth={1.6} dot={false}
                style={{ filter: `drop-shadow(0 0 4px ${up ? "#39ff14" : "#ff003c"})` }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-full flex items-center justify-center panel-label text-[9px] text-center">
            NO PNL YET · START THE ENGINE
          </div>
        )}
      </div>
      <div className="flex justify-between panel-label text-[8px] mt-1">
        <span>START $10,000</span>
        <span>NOW ${last.toFixed(2)}</span>
      </div>
    </div>
  );
}
