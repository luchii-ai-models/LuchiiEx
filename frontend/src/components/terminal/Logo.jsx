// Official Luchii Ex emblem (transparent PNG).
import logoUrl from "../../assets/luchii-logo.png";

export default function Logo({ size = 44, variant = "mark", className = "" }) {
  return (
    <img
      data-testid="brand-logo"
      src={logoUrl}
      alt="Luchii Ex"
      width={size}
      height={size}
      className={`rounded-full shrink-0 ${className}`}
      style={{
        width: size,
        height: size,
        objectFit: "contain",
        filter: "drop-shadow(0 0 6px rgba(0,229,255,0.45))",
      }}
    />
  );
}
