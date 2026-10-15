import { motion } from "framer-motion";
import logoUrl from "../../assets/luchii-logo.png";

// Minimal, professional open screen — just the brand logo on black.
export default function SplashScreen() {
  return (
    <motion.div
      data-testid="splash-screen"
      className="fixed inset-0 z-[999] flex items-center justify-center bg-black"
      initial={{ opacity: 1 }}
      exit={{ opacity: 0, transition: { duration: 0.5 } }}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.92 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.6, ease: "easeOut" }}
      >
        <img
          src={logoUrl}
          alt="Frasberg AI · Luchii"
          className="rounded-full animate-spin-slow"
          style={{ width: 150, height: 150, filter: "drop-shadow(0 0 24px rgba(60,140,255,0.5))" }}
        />
      </motion.div>
    </motion.div>
  );
}
