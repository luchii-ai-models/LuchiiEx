// Official Frasberg AI · Luchii emblem.
import logoUrl from "../../assets/luchii-logo.png";

export default function Logo({ size = 44, variant = "mark", className = "" }) {
  return (
    <img
      data-testid="brand-logo"
      src={logoUrl}
      alt="Frasberg AI · Luchii"
      width={size}
      height={size}
      className={`rounded-full shrink-0 ${className}`}
      style={{
        width: size,
        height: size,
        objectFit: "contain",
        filter: "drop-shadow(0 0 6px rgba(0,120,255,0.35))",
      }}
    />
  );
}
