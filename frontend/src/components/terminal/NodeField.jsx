import { useEffect, useRef } from "react";

// Animated bull/bear "force graph" node field on canvas.
export default function NodeField({ logs }) {
  const canvasRef = useRef(null);
  const nodesRef = useRef([]);
  const rafRef = useRef(null);

  // bias colors based on recent decisions
  const recent = (logs || []).slice(0, 10);
  const buys = recent.filter((l) => l.action === "BUY").length;
  const sells = recent.filter((l) => l.action === "SELL").length;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const resize = () => {
      canvas.width = canvas.offsetWidth;
      canvas.height = canvas.offsetHeight;
    };
    resize();
    window.addEventListener("resize", resize);

    if (nodesRef.current.length === 0) {
      nodesRef.current = Array.from({ length: 54 }, () => ({
        x: Math.random(), y: Math.random(),
        vx: (Math.random() - 0.5) * 0.0006, vy: (Math.random() - 0.5) * 0.0006,
        r: 1.5 + Math.random() * 5,
        bull: Math.random() > 0.5,
        phase: Math.random() * Math.PI * 2,
      }));
    }

    const draw = () => {
      const W = canvas.width, H = canvas.height;
      ctx.clearRect(0, 0, W, H);
      const t = Date.now() / 1000;
      const nodes = nodesRef.current;
      // faint links
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const dx = (nodes[i].x - nodes[j].x) * W, dy = (nodes[i].y - nodes[j].y) * H;
          const d = Math.hypot(dx, dy);
          if (d < 70) {
            ctx.strokeStyle = `rgba(255,214,10,${0.04 * (1 - d / 70)})`;
            ctx.beginPath();
            ctx.moveTo(nodes[i].x * W, nodes[i].y * H);
            ctx.lineTo(nodes[j].x * W, nodes[j].y * H);
            ctx.stroke();
          }
        }
      }
      nodes.forEach((n) => {
        n.x += n.vx; n.y += n.vy;
        if (n.x < 0 || n.x > 1) n.vx *= -1;
        if (n.y < 0 || n.y > 1) n.vy *= -1;
        const px = n.x * W, py = n.y * H;
        const pulse = 0.6 + 0.4 * Math.sin(t * 2 + n.phase);
        const color = n.bull ? "57,255,20" : "255,0,60";
        const glow = ctx.createRadialGradient(px, py, 0, px, py, n.r * 4);
        glow.addColorStop(0, `rgba(${color},${0.55 * pulse})`);
        glow.addColorStop(1, `rgba(${color},0)`);
        ctx.fillStyle = glow;
        ctx.beginPath(); ctx.arc(px, py, n.r * 4, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = `rgba(${color},${pulse})`;
        ctx.beginPath(); ctx.arc(px, py, n.r, 0, Math.PI * 2); ctx.fill();
      });
      rafRef.current = requestAnimationFrame(draw);
    };
    draw();
    return () => { cancelAnimationFrame(rafRef.current); window.removeEventListener("resize", resize); };
  }, []);

  // rebalance bull/bear count from signals
  useEffect(() => {
    const nodes = nodesRef.current;
    if (!nodes.length) return;
    const targetBull = 0.5 + (buys - sells) * 0.04;
    nodes.forEach((n, i) => { n.bull = (i / nodes.length) < targetBull; });
  }, [buys, sells]);

  const legend = [
    ["BULL", "#39ff14"], ["BEAR", "#ff003c"], ["MEDIAN", "#00f0ff"], ["CATALYST", "#ffd60a"],
  ];

  return (
    <div className="panel flex-1 min-h-0 relative overflow-hidden">
      <div className="absolute top-2 left-2 z-10 flex items-center gap-3">
        <span className="panel-label">◈ MINOFISH · BTC FORCE GRAPH</span>
      </div>
      <div className="absolute top-2 right-2 z-10 flex flex-col gap-0.5">
        {legend.map(([l, c]) => (
          <div key={l} className="flex items-center gap-1.5 justify-end">
            <span className="panel-label text-[8px]">{l}</span>
            <span className="h-2 w-2 rounded-full" style={{ background: c, boxShadow: `0 0 6px ${c}` }} />
          </div>
        ))}
      </div>
      <div className="absolute bottom-2 left-2 z-10 panel-label text-[8px]">
        BULL {buys} · BEAR {sells} · NODES 54
      </div>
      <canvas ref={canvasRef} className="w-full h-full block" />
    </div>
  );
}
