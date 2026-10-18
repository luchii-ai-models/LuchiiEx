import { useParams, useNavigate, Navigate, Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import Logo from "../components/terminal/Logo";
import Footer from "../components/terminal/Footer";

const CONTENT = {
  terms: {
    title: "Terms of Service",
    intro: "These terms govern your use of the Frasberg Luchii trading platform. By accessing the terminal you agree to them.",
    sections: [
      ["Use of the platform", "Luchii provides autonomous and assisted trading tooling. DRY (paper) mode is the default. You are responsible for any account you connect and any live orders you enable."],
      ["No financial advice", "Signals, reasoning and analytics are informational only and are not financial advice. Markets carry risk and past performance does not guarantee future results."],
      ["Accounts", "You must keep your operator credentials and any broker keys secure. You may not share access or attempt to disrupt the service."],
      ["Changes", "We may update these terms as the platform evolves. Continued use constitutes acceptance of the latest version."],
    ],
  },
  privacy: {
    title: "Privacy Policy",
    intro: "We respect your privacy and collect only what we need to run the terminal.",
    sections: [
      ["What we collect", "Account details (email, name), configuration you set, and messages you send to Luchii or the contact form. Broker keys you enter are stored to enable mirroring."],
      ["How we use it", "To operate the trading engine, personalise your terminal, save your chat history across devices, and improve the product."],
      ["Sharing", "We do not sell your personal information. Data is shared only with the infrastructure providers required to run the service."],
      ["Your controls", "You can delete conversations and request removal of your data at any time via the contact form."],
    ],
  },
  security: {
    title: "Security",
    intro: "Security is core to a trading platform. Here is how we protect your account.",
    sections: [
      ["Authentication", "Access is protected by JWT-based operator login. Sessions expire and credentials are never stored in plain text."],
      ["Risk guardrails", "A kill-switch, daily loss limit, maximum order size and a manual-confirmation toggle protect you from runaway execution."],
      ["Live vs paper", "LIVE mode is off by default. Real orders only fire once you explicitly connect an exchange and enable live trading."],
      ["Reporting", "Found an issue? Reach us through the contact form on the company page and we will respond promptly."],
    ],
  },
  status: {
    title: "System Status",
    intro: "Live operational status of Frasberg Luchii services.",
    sections: [
      ["Trading engine", "Operational — the Luchii engine is generating BTC signals."],
      ["Market data", "Operational — Coinbase market feed streaming."],
      ["Luchii AI", "Operational — Luchii 4.7, Pro and Mini available (subject to model capacity)."],
      ["Broker mirroring", "Operational — MetaTrader 4/5 and PocketOption connectors available."],
    ],
  },
  community: {
    title: "Community",
    intro: "Join operators building with Luchii.",
    sections: [
      ["Forums", "Share strategies, guardrail presets and broker setups with other operators."],
      ["Feedback", "Your ideas shape the roadmap. Send feature requests through the contact form."],
      ["Guidelines", "Be respectful, never share credentials, and keep discussions constructive."],
    ],
  },
  docs: {
    title: "Documentation · Platform Features",
    intro: "Everything the Frasberg Luchii trading platform can do. Luchii is the flagship text-generation engine powering the Frasberg AI ecosystem.",
    sections: [
      ["Autonomous trading bot", "The Luchii engine analyses live BTC and generates BUY/SELL signals with human-readable reasoning. Start/stop it from the dashboard; DRY mode paper-trades by default."],
      ["Risk guardrails", "Kill-switch, daily loss limit, max order size and manual-confirm — all in Settings › Guardrails."],
      ["Broker mirroring", "Connect MetaTrader 4/5 (MetaApi token + login/password/server) and PocketOption (SSID) in Settings › Brokers to mirror signals in real time."],
      ["Luchii Chat", "An askgpt-style assistant with Luchii 4.7, Pro and Mini models. Conversations are saved to your account across devices."],
      ["Luchii Engine API", "Base URL https://api.frasbergai.com/luchii/v1/ · Bearer auth + X-Luchii-Client. Endpoints include GET /market/scan, POST /market/signal, POST /model/token and POST /keys/api."],
    ],
  },
  capabilities: {
    title: "Autonomous Cloud Capabilities",
    intro: "The Luchii engine runs governed, multi-region inference for autonomous decisioning.",
    sections: [
      ["Multi-region inference", "Low-latency model serving across regions with automatic failover for always-on decisioning."],
      ["Governed model behavior", "Constitutional safety and policy guardrails constrain every decision the engine makes."],
      ["Tool-call orchestration", "The engine orchestrates market scans, signal generation and broker actions as governed tool calls."],
      ["Global deployment", "Deploy to the Frasberg autonomous cloud with unlimited API calls, standard compliance and managed storage."],
    ],
  },
  pricing: {
    title: "Pricing & Plans",
    intro: "Simple base fee plus usage. Choose the tier that matches your scale.",
    sections: [
      ["Plans", "Basic · Pro · Enterprise · Sovereign · Ultra — from self-serve to fully sovereign deployments with custom pricing available."],
      ["Included", "Unlimited API calls, automatic failover, standard compliance, 100 GB storage and email support on every paid tier."],
      ["Usage charges", "Decisions $0.01 · Actions $0.05 · Recovery events $0.10 · Optimization credits −$0.02. You only pay for what the engine does."],
      ["Enterprise & Sovereign", "Dedicated capacity, custom compliance and volume pricing — talk to us via the contact form on the company page."],
    ],
  },
  architecture: {
    title: "Architecture",
    intro: "How the Frasberg Luchii platform is built.",
    sections: [
      ["System architecture", "A governed inference core (Luchii engine) fronted by regional gateways, an orchestration layer for tool calls, and a control plane for policy and billing."],
      ["Key components", "Model serving, governance/constitution engine, tool-call orchestrator, market-data ingestion, and broker mirroring connectors."],
      ["Data model", "Entities include operators, bots, conversations, decisions and actions — each versioned and auditable at scale."],
      ["Technology stack", "Python inference services, a React/console front end, MongoDB persistence, and Kubernetes/Helm for autonomous cloud deployment."],
    ],
  },
  cookies: {
    title: "Manage Cookies",
    intro: "We use minimal cookies to keep you signed in and remember your preferences.",
    sections: [
      ["Essential", "Required for authentication and to keep the terminal working. These cannot be disabled."],
      ["Preferences", "Remember your layout choices locally in your browser."],
      ["Analytics", "Aggregate, anonymous usage to improve the product. You can opt out below."],
    ],
  },
  "do-not-share": {
    title: "Do Not Share My Personal Information",
    intro: "You have the right to opt out of the sharing of your personal information.",
    sections: [
      ["Your right", "Frasberg does not sell your personal information. You may still request that we limit any sharing with infrastructure providers to the minimum required to operate the service."],
      ["How to opt out", "Submit a request through the contact form on the company page with the subject 'Do Not Share'. We will honour it within 30 days."],
    ],
  },
};

export default function InfoPage() {
  const { slug } = useParams();
  const navigate = useNavigate();
  const data = CONTENT[slug];
  if (!data) return <Navigate to="/info/docs" replace />;

  return (
    <div className="min-h-screen w-full bg-black text-white flex flex-col font-mono">
      <header className="shrink-0 border-b border-term-border bg-term-surface px-3 sm:px-4 py-2 flex items-center justify-between">
        <button data-testid="info-back-button" onClick={() => navigate(-1)}
          className="flex items-center gap-2 text-term-muted hover:text-neon-magenta transition-colors text-xs tracking-[0.2em] uppercase">
          <ArrowLeft size={14} /> Back
        </button>
        <span className="panel-label">◈ {data.title.toUpperCase()}</span>
      </header>

      <main className="flex-1 overflow-y-auto px-6 py-10">
        <nav data-testid="info-tabs" className="flex flex-wrap justify-center gap-x-4 gap-y-2 mb-8 max-w-3xl mx-auto">
          {Object.entries(CONTENT).map(([s, c]) => (
            <Link key={s} to={`/info/${s}`} data-testid={`info-tab-${s}`}
              className={`text-[10px] tracking-[0.15em] uppercase px-2 py-1 border transition-colors ${s === slug ? "border-neon-magenta text-neon-magenta" : "border-transparent text-term-muted hover:text-white"}`}>
              {c.title}
            </Link>
          ))}
          <Link to="/help" data-testid="info-tab-help" className="text-[10px] tracking-[0.15em] uppercase px-2 py-1 border border-transparent text-term-muted hover:text-white">Help center</Link>
          <Link to="/company" data-testid="info-tab-company" className="text-[10px] tracking-[0.15em] uppercase px-2 py-1 border border-transparent text-term-muted hover:text-white">About the company</Link>
        </nav>
        <div className="w-full max-w-2xl mx-auto" data-testid={`info-${slug}`}>
          <div className="flex items-center gap-3 mb-6">
            <Logo size={44} />
            <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tighter">{data.title}</h1>
          </div>
          <p className="text-sm text-term-muted leading-relaxed mb-8">{data.intro}</p>
          <div className="space-y-5">
            {data.sections.map(([h, b], i) => (
              <div key={i} className="panel p-5">
                <div className="text-sm font-bold text-neon-magenta mb-1.5">{h}</div>
                <p className="text-sm text-white/80 leading-relaxed">{b}</p>
              </div>
            ))}
          </div>
        </div>
      </main>

      <Footer />
    </div>
  );
}
