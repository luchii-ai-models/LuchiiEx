import "./App.css";
import { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { AnimatePresence } from "framer-motion";
import { Toaster } from "sonner";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { ThemeProvider, useTheme } from "./context/ThemeContext";
import Login from "./pages/Login";
import Logo from "./components/terminal/Logo";
import Dashboard from "./pages/Dashboard";
import Company from "./pages/Company";
import HelpCenter from "./pages/HelpCenter";
import InfoPage from "./pages/InfoPage";
import SplashScreen from "./components/terminal/SplashScreen";

// Single source of truth for the theme class: light everywhere EXCEPT the
// landing/auth screen, which is always dark.
function ThemeApplier() {
  const { theme } = useTheme();
  const location = useLocation();
  useEffect(() => {
    const isAuth = location.pathname === "/login";
    document.documentElement.classList.toggle("light", theme === "light" && !isAuth);
  }, [theme, location.pathname]);
  return null;
}

function Protected({ children }) {
  const { user, checking } = useAuth();
  if (checking)
    return (
      <div className="h-screen w-screen bg-black flex flex-col items-center justify-center gap-4" data-testid="boot-screen">
        <Logo size={96} className="animate-spin-slow" />
        <span className="text-neon-magenta text-glow-magenta text-[10px] tracking-[0.3em] animate-pulse">
          LUCHII EX<span className="animate-blink">_</span>
        </span>
      </div>
    );
  return user ? children : <Navigate to="/login" replace />;
}

function App() {
  const [showSplash, setShowSplash] = useState(() => !sessionStorage.getItem("luchii_splash_seen"));

  useEffect(() => {
    if (!showSplash) return;
    sessionStorage.setItem("luchii_splash_seen", "1");
    const t = setTimeout(() => setShowSplash(false), 2000);
    return () => clearTimeout(t);
  }, [showSplash]);

  return (
    <div className="App grain">
      <ThemeProvider>
        <AnimatePresence>{showSplash && <SplashScreen key="splash" />}</AnimatePresence>
        <AuthProvider>
          <BrowserRouter>
            <ThemeApplier />
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/info/:slug" element={<InfoPage />} />
              <Route path="/" element={<Protected><Dashboard /></Protected>} />
              <Route path="/company" element={<Protected><Company /></Protected>} />
              <Route path="/help" element={<Protected><HelpCenter /></Protected>} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </BrowserRouter>
          <Toaster theme="dark" position="top-right" toastOptions={{
            style: { background: "#050505", border: "1px solid #ffd60a", color: "#fff",
                     fontFamily: "'JetBrains Mono', monospace", borderRadius: 0 } }} />
        </AuthProvider>
      </ThemeProvider>
    </div>
  );
}

export default App;
