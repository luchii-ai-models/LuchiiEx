import { Link } from "react-router-dom";

export default function Footer() {
  return (
    <footer data-testid="app-footer"
      className="shrink-0 border-t border-term-border bg-term-surface px-3 sm:px-4 py-2 flex flex-col items-center gap-1.5">
      <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1" data-testid="footer-info-links">
        <Link to="/info/capabilities" data-testid="footer-info-link"
          className="text-[9px] tracking-[0.12em] text-term-muted hover:text-neon-magenta transition-colors">Info · Docs · Terms · Privacy · Status</Link>
        <Link to="/help" data-testid="footer-help-link"
          className="text-[9px] tracking-[0.12em] text-term-muted hover:text-neon-cyan transition-colors">Help center</Link>
        <Link to="/company" data-testid="footer-company-link"
          className="text-[9px] tracking-[0.12em] text-term-muted hover:text-neon-cyan transition-colors">About the company</Link>
      </div>
      <span className="text-[10px] tracking-[0.18em] text-neon-magenta text-glow-magenta">
        © 2026, Frasberg, Inc. or its affiliates. All rights reserved.
      </span>
    </footer>
  );
}
