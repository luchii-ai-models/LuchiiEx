import { Link } from "react-router-dom";

export default function Footer() {
  return (
    <footer data-testid="app-footer"
      className="shrink-0 border-t border-term-border bg-term-surface px-3 sm:px-4 py-2 flex items-center justify-center">
      <Link to="/info/capabilities" data-testid="footer-company-link" title="Info · Docs · Terms · Privacy · Help · Company"
        className="text-[10px] tracking-[0.18em] text-neon-magenta text-glow-magenta hover:text-white transition-colors">
        © 2026, Frasberg, Inc.
      </Link>
    </footer>
  );
}
