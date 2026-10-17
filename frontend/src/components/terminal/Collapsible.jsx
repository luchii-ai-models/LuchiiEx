import { useState } from "react";
import { ChevronDown } from "lucide-react";

// Drop-down tab wrapper: click the header bar to collapse/expand any dashboard card.
export default function Collapsible({ id, title, defaultOpen = true, children }) {
  const key = `luchii.card.${id}`;
  const [open, setOpen] = useState(() => {
    const v = localStorage.getItem(key);
    return v === null ? defaultOpen : v === "1";
  });
  const toggle = () => { localStorage.setItem(key, open ? "0" : "1"); setOpen(!open); };
  return (
    <div data-testid={`collapsible-${id}`} className="shrink-0">
      <button data-testid={`collapsible-${id}-toggle`} onClick={toggle} aria-expanded={open}
        className="w-full flex items-center justify-between px-2 py-1 border border-term-border bg-term-surface text-[9px] tracking-[0.2em] text-term-muted hover:text-neon-magenta transition-colors">
        <span>▾ {title}</span>
        <ChevronDown size={12} className={`transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && <div className="mt-1" data-testid={`collapsible-${id}-body`}>{children}</div>}
    </div>
  );
}
