import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { formatApiErrorDetail } from "../lib/api";
import { Eye, EyeOff } from "lucide-react";
import Footer from "../components/terminal/Footer";
import Logo from "../components/terminal/Logo";

export default function Login() {
  const { login, register, user } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [help, setHelp] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password, name);
      navigate("/", { replace: true });
    } catch (err) {
      setError(formatApiErrorDetail(err.response?.data?.detail) || err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="h-screen w-screen bg-black flex items-center justify-center overflow-hidden relative">
      <div className="absolute inset-0 opacity-[0.06]"
           style={{ backgroundImage: "linear-gradient(#ffd60a 1px, transparent 1px), linear-gradient(90deg, #ffd60a 1px, transparent 1px)", backgroundSize: "42px 42px" }} />
      <div className="absolute top-0 left-0 w-full h-[2px] bg-neon-magenta/40 animate-scan" />

      <div className="relative z-10 w-full max-w-md px-6">
        <div className="flex justify-center mb-8">
          <Logo size={96} className="animate-spin-slow" />
        </div>

        <div className="panel panel-glow animate-pulse-glow p-6">
          <div className="flex items-center justify-center mb-6">
            <span className="text-2xl sm:text-3xl font-extrabold tracking-tight text-white" data-testid="login-title">LUCHII <span className="text-white/90">EX</span></span>
          </div>

          <form onSubmit={submit} className="space-y-5">
            {mode === "register" && (
              <Field label="CALLSIGN" value={name} onChange={setName} placeholder="operator_01" testid="register-name-input" />
            )}
            <Field label="EMAIL" type="email" value={email} onChange={setEmail} required testid="login-email-input" />
            <Field label="PASSWORD" type="password" value={password} onChange={setPassword} placeholder="••••••••" required testid="login-password-input" />

            {error && <div data-testid="login-error" className="text-neon-red text-xs border border-neon-red/40 bg-neon-red/5 px-3 py-2">{error}</div>}

            <button data-testid="login-submit-button" disabled={busy}
              className="w-full border border-neon-magenta text-neon-magenta hover:bg-neon-magenta hover:text-black transition-colors duration-200 py-3 text-sm tracking-[0.2em] uppercase flex items-center justify-center gap-2 disabled:opacity-40">
              {busy ? "SIGNING IN…" : mode === "login" ? "LOGIN" : "CREATE ACCOUNT"}
            </button>
          </form>

          <div className="mt-5 text-center text-xs text-term-muted">
            {mode === "login" ? "Create new account?" : "Already have an account?"}{" "}
            <button data-testid="toggle-auth-mode" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}
              className="text-neon-cyan hover:text-glow-cyan underline underline-offset-4">
              {mode === "login" ? "REGISTER" : "LOGIN"}
            </button>
          </div>
          <div className="mt-2 text-right">
            <button type="button" data-testid="help-with-login" onClick={() => setHelp(!help)} className="text-[10px] text-term-muted hover:text-white underline underline-offset-4">Help with Login</button>
          </div>
          {help && (
            <div data-testid="login-help-panel" className="mt-3 border border-term-border p-3 text-[11px] text-white/80 leading-relaxed space-y-1">
              <p><b>Forgot password?</b> Email <a className="text-neon-cyan" href="mailto:support@frasberg.com?subject=Luchii%20Ex%20password%20reset">support@frasberg.com</a> from your account email and we will send a reset link.</p>
              <p><b>No access to your old email?</b> Email us from your new address with your account email and the last 4 digits of your connected exchange account; we verify ownership and move the account to the new email.</p>
              <p><b>Email change:</b> same process — verification first, then we update the login email. Never share your password or exchange API keys with anyone.</p>
            </div>
          )}
        </div>
      </div>

      <div className="absolute bottom-0 left-0 right-0 z-10">
        <Footer />
      </div>
    </div>
  );
}

function Field({ label, value, onChange, type = "text", placeholder, required, testid }) {
  const [show, setShow] = useState(false);
  const isPw = type === "password";
  return (
    <label className="block relative">
      <span className="panel-label">{label}</span>
      <input
        data-testid={testid}
        type={isPw && show ? "text" : type}
        value={value}
        required={required}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className={`mt-2 w-full bg-transparent border-b border-neon-magenta/60 focus:border-neon-magenta outline-none py-2 text-white text-sm placeholder:text-term-muted/40 ${isPw ? "pr-8" : ""}`}
      />
      {isPw && (
        <button type="button" data-testid={`${testid}-toggle-visibility`} onClick={() => setShow(!show)} title={show ? "Hide password" : "Show password"}
          className="absolute right-0 bottom-2 text-white/80 hover:text-white transition-colors">
          {show ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      )}
    </label>
  );
}
