import { BarChart, Bar, ResponsiveContainer, Cell } from "recharts";

export default function Analytics({ trades, bot }) {
  const rows = (trades || []).slice(0, 16).reverse();
  const volData = rows.map((t, i) => ({ i, v: Math.abs(t.qty * t.price) }));
  const pnlData = rows.filter((t) => t.side === "SELL").map((t, i) => ({ i, v: t.pnl }));

  const totalVol = volData.reduce((a, b) => a + b.v, 0);

  return (
    <div className="panel p-2 flex flex-col min-h-0">
      <span className="panel-label">◈ ANALYTICS</span>
      <div className="mt-1">
        <div className="panel-label text-[8px]">VOLUME ${totalVol.toFixed(0)}</div>
        <div className="h-10">
          {volData.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={volData}><Bar dataKey="v" fill="#ffd60a" /></BarChart>
            </ResponsiveContainer>
          ) : <Empty />}
        </div>
      </div>
      <div className="mt-2">
        <div className="panel-label text-[8px]">TRADE P&L</div>
        <div className="h-10">
          {pnlData.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={pnlData}>
                <Bar dataKey="v">
                  {pnlData.map((d, i) => <Cell key={i} fill={d.v >= 0 ? "#39ff14" : "#ff003c"} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : <Empty />}
        </div>
      </div>
    </div>
  );
}

const Empty = () => (
  <div className="h-full flex items-center panel-label text-[8px]">AWAITING DATA</div>
);
