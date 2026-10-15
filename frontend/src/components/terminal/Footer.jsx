import { Link } from "react-router-dom";

const INFO_LINKS = [
  ["Capabilities", "capabilities"], ["Pricing", "pricing"], ["Architecture", "architecture"],
  ["Docs", "docs"], ["Terms", "terms"], ["Privacy", "privacy"], ["Security", "security"],
  ["Status", "status"], ["Community", "community"], ["Manage cookies", "cookies"],
];

export default function Footer() {
  return (
    <footer data-testid="app-footer"
      className="shrink-0 border-t border-term-border bg-term-surface px-3 sm:px-4 py-2 flex flex-col items-center gap-1.5">
      <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1" data-testid="footer-info-links">
        {INFO_LINKS.map(([label, slug]) => (
          <Link key={slug} to={`/info/${slug}`} data-testid={`footer-link-${slug}`}
            className="text-[9px] tracking-[0.12em] text-term-muted hover:text-neon-magenta transition-colors">
            {label}
          </Link>
        ))}
        <Link to="/help" data-testid="footer-help-link"
          className="text-[9px] tracking-[0.12em] text-term-muted hover:text-neon-cyan transition-colors">Help center</Link>
        <Link to="/company" data-testid="footer-company-link"
          className="text-[9px] tracking-[0.12em] text-term-muted hover:text-neon-cyan transition-colors">About the company</Link>
        <Link to="/info/do-not-share" data-testid="footer-link-do-not-share"
          className="text-[9px] tracking-[0.12em] text-term-muted hover:text-neon-magenta transition-colors">Do not share my personal information</Link>
      </div>
      <span className="text-[10px] tracking-[0.18em] text-neon-magenta text-glow-magenta">
        © 2026, Frasberg, Inc. or its affiliates. All rights reserved.
      </span>
    </footer>
  );
}
