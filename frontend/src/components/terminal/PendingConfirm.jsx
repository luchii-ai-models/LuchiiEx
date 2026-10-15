import api from "../../lib/api";
import { toast } from "sonner";
import { Check, X } from "lucide-react";

export default function PendingConfirm({ pending, refresh }) {
  if (!pending) return null;

  const act = async (fn, ok) => {
    try { await fn(); toast.success(ok); refresh(); }
    catch (e) { toast.error(e.response?.data?.detail || "Failed"); }
  };

  return (
    <div data-testid="pending-confirm-banner"
      className="shrink-0 border-y border-neon-amber bg-neon-amber/10 px-4 py-2 flex items-center justify-between animate-pulse-glow">
      <div className="text-xs flex items-center gap-3">
        <span className="text-neon-amber tracking-[0.2em] font-bold">⏳ CONFIRM LIVE ORDER</span>
        <span className={pending.action === "BUY" ? "text-neon-green font-bold" : "text-neon-red font-bold"}>{pending.action}</span>
        <span className="tnum text-white">BTC @ ${pending.price?.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
        <span className="tnum text-term-muted">SIZE ${pending.size_usd}</span>
        <span className="text-white/70 hidden md:inline truncate max-w-md">{pending.reason}</span>
      </div>
      <div className="flex items-center gap-2">
        <button data-testid="approve-order-button" onClick={() => act(() => api.post("/bot/pending/approve"), "Order approved")}
          className="border border-neon-green text-neon-green hover:bg-neon-green hover:text-black transition-colors px-3 py-1.5 text-xs tracking-[0.15em] flex items-center gap-1">
          <Check size={12} /> APPROVE
        </button>
        <button data-testid="reject-order-button" onClick={() => act(() => api.post("/bot/pending/reject"), "Order rejected")}
          className="border border-neon-red text-neon-red hover:bg-neon-red hover:text-black transition-colors px-3 py-1.5 text-xs tracking-[0.15em] flex items-center gap-1">
          <X size={12} /> REJECT
        </button>
      </div>
    </div>
  );
}
