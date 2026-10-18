import { useMemo, useRef, useState } from "react";
import { ZoomIn, ZoomOut, RotateCcw, Maximize2, X } from "lucide-react";

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

export default function CandleChart({ snapshot, bot, full = false, onToggleFull }) {
  const candles = snapshot?.candles || [];
  const price = snapshot?.price;
  const total = candles.length;

  const [count, setCount] = useState(70); // how many candles visible
  const [offset, setOffset] = useState(0); // how many candles hidden on the right (pan)
  const touch = useRef({ dist: 0, x: 0, mode: null });

  const visibleCount = clamp(count, 15, Math.max(15, total));
  const end = clamp(total - offset, visibleCount, total);
  const start = Math.max(0, end - visibleCount);
  const view = candles.slice(start, end);

  const zoom = (dir) => setCount((c) => clamp(c + dir * 10, 15, Math.max(15, total)));
  const resetView = () => { setCount(70); setOffset(0); };

  const onWheel = (e) => {
    setCount((c) => clamp(c + (e.deltaY > 0 ? 6 : -6), 15, Math.max(15, total)));
  };

  const pan = (dxCandles) => {
    setOffset((o) => clamp(Math.round(o + dxCandles), 0, Math.max(0, total - visibleCount)));
  };

  const onTouchStart = (e) => {
    if (e.touches.length === 2) {
      const dx = e.touches[0].clientX - e.touches[1].clientX;
      const dy = e.touches[0].clientY - e.touches[1].clientY;
      touch.current = { dist: Math.hypot(dx, dy), x: 0, mode: "pinch" };
    } else if (e.touches.length === 1) {
      touch.current = { dist: 0, x: e.touches[0].clientX, mode: "pan" };
    }
  };

  const onTouchMove = (e) => {
    const t = touch.current;
    if (t.mode === "pinch" && e.touches.length === 2) {
      const dx = e.touches[0].clientX - e.touches[1].clientX;
      const dy = e.touches[0].clientY - e.touches[1].clientY;
      const d = Math.hypot(dx, dy);
      if (t.dist) {
        const ratio = d / t.dist;
        if (Math.abs(ratio - 1) > 0.04) {
          setCount((c) => clamp(Math.round(c / ratio), 15, Math.max(15, total)));
          touch.current = { ...t, dist: d };
        }
      }
    } else if (t.mode === "pan" && e.touches.length === 1) {
      const x = e.touches[0].clientX;
      const dxPx = x - t.x;
      const pxPerCandle = 380 / visibleCount;
      if (Math.abs(dxPx) >= pxPerCandle) {
        pan(dxPx / pxPerCandle); // drag right -> older candles
        touch.current = { ...t, x };
      }
    }
  };

  const { rects, min, max, w, h, pad } = useMemo(() => {
    const w = 600, h = 240, pad = 4;
    if (!view.length) return { rects: [], min: 0, max: 0, w, h, pad };
    const lows = view.map((c) => c.l), highs = view.map((c) => c.h);
    const min = Math.min(...lows), max = Math.max(...highs);
    const range = max - min || 1;
    const cw = (w - pad * 2) / view.length;
    const y = (v) => pad + (1 - (v - min) / range) * (h - pad * 2);
    const rects = view.map((c, i) => {
      const x = pad + i * cw + cw / 2;
      const up = c.c >= c.o;
      return { x, cw, up, wickTop: y(c.h), wickBot: y(c.l),
        bodyTop: y(Math.max(c.o, c.c)), bodyH: Math.max(1, Math.abs(y(c.o) - y(c.c))) };
    });
    return { rects, min, max, w, h, pad };
  }, [view]);

  const zoomBtn = "h-6 w-6 flex items-center justify-center border border-term-border text-term-muted hover:text-neon-magenta hover:border-neon-magenta transition-colors";

  return (
    <div className={full ? "fixed inset-0 z-[200] bg-black p-2 flex flex-col" : "panel p-2 shrink-0"} style={full ? { position: "fixed" } : undefined} data-testid={full ? "chart-fullscreen" : "chart-card"}>
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <span className="panel-label">◈ BTC/USD · 1M</span>
          <span className="tnum text-lg font-extrabold text-neon-magenta text-glow-magenta">
            {price ? `$${price.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : "—"}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <button data-testid="chart-zoom-in" onClick={() => zoom(-1)} className={zoomBtn} title="Zoom in"><ZoomIn size={12} /></button>
          <button data-testid="chart-zoom-out" onClick={() => zoom(1)} className={zoomBtn} title="Zoom out"><ZoomOut size={12} /></button>
          <button data-testid="chart-zoom-reset" onClick={resetView} className={zoomBtn} title="Reset"><RotateCcw size={11} /></button>
          <button data-testid={full ? "chart-fullscreen-close" : "chart-fullscreen-open"} onClick={onToggleFull} className={zoomBtn} title={full ? "Close" : "Pop-out full screen"}>
            {full ? <X size={12} /> : <Maximize2 size={12} />}
          </button>
          <span className={`ml-1 px-1.5 py-0.5 text-[9px] tracking-[0.25em] border ${bot?.status === "running" ? "border-neon-green text-neon-green" : "border-neon-cyan text-neon-cyan"}`}>
            {bot?.status === "running" ? "FEED" : "STANDBY"}
          </span>
        </div>
      </div>
      <svg data-testid="candle-chart-svg" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"
        className={`w-full touch-none select-none ${full ? "flex-1 min-h-0" : ""}`} style={full ? { height: "calc(100vh - 80px)" } : { height: "clamp(140px, 22vh, 240px)" }}
        onWheel={onWheel} onTouchStart={onTouchStart} onTouchMove={onTouchMove}>
        <defs>
          <linearGradient id="grid" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#0d0d0f" /><stop offset="100%" stopColor="#050505" />
          </linearGradient>
        </defs>
        <rect x="0" y="0" width={w} height={h} fill="url(#grid)" />
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1="0" x2={w} y1={h * f} y2={h * f} stroke="#141416" strokeWidth="1" />
        ))}
        {rects.map((r, i) => (
          <g key={i}>
            <line x1={r.x} x2={r.x} y1={r.wickTop} y2={r.wickBot} stroke={r.up ? "#39ff14" : "#ff003c"} strokeWidth="1" opacity="0.9" />
            <rect x={r.x - r.cw * 0.32} y={r.bodyTop} width={r.cw * 0.64} height={r.bodyH}
              fill={r.up ? "#39ff14" : "#ff003c"} opacity="0.92" />
          </g>
        ))}
        {price && max > min && (
          <line x1="0" x2={w} y1={pad + (1 - (price - min) / (max - min)) * (h - pad * 2)}
            y2={pad + (1 - (price - min) / (max - min)) * (h - pad * 2)}
            stroke="#ffd60a" strokeWidth="1" strokeDasharray="4 3" opacity="0.8" />
        )}
      </svg>
      <div className="flex justify-between panel-label text-[8px] mt-1">
        <span>L ${min ? min.toLocaleString() : "—"}</span>
        <span>RSI {snapshot?.rsi ?? "—"} · {visibleCount} bars</span>
        <span>H ${max ? max.toLocaleString() : "—"}</span>
      </div>
    </div>
  );
}
