# Frasberg | Luchii Trading Bot — PRD

## Original problem statement
"clone all frontend and backend wire back system as is." Source: https://trade-bridge-73.preview.emergentagent.com/login. Env values given by the user: ADMIN_EMAIL admin@gravia.io, ADMIN_PASSWORD Gravia@2026, BOT_TICK_SECONDS 12, ENCRYPTION_KEY, JWT_SECRET, TELEGRAM_BOT_TOKEN.
User choices: copy from trade-bridge-73; use the env values exactly; no Emergent Universal Key (keep the Luchii model names); a 1:1 copy with no differences.

## Architecture
- Frontend: original source pulled verbatim from the live site's source map (Login, Dashboard, Chat, Help, Company, Info pages; terminal components; settings tabs; WalletConnect), plus the logo, icons, manifest and service worker. The Tailwind config was rebuilt with the neon/term colours and animations.
- Backend (FastAPI): server.py (routes), auth.py (JWT + bcrypt, login lockout), engine.py (ticks every 12s: SMA7/25 + RSI signal, live ccxt orders, P&L, kill switch, daily loss limit, manual confirm, broker mirroring), market.py (Coinbase snapshot every 4s), exchange_service.py (15 exchanges, keys encrypted with Fernet), brokers.py (MetaApi REST), pocket.py (BinaryOptionsToolsV2), alerts.py (Telegram)

## User personas
- Operator: connects their own exchange and runs the bot on real funds
- Admin: the seeded admin account

## Core requirements
- Live-only trading: no DRY/paper mode and no virtual wallet
- Guardrails off by default (max_order_usd=0, daily_loss_limit=0, manual_confirm=false)
- No Claude/Anthropic/Emergent text anywhere
- Footer: "© 2026, Frasberg, Inc. or its affiliates. All rights reserved."

## Implemented (2026-10-15)
- Full frontend and backend clone; iteration_1 passed 31/31 backend tests and all UI flows
- Chat and help return "temporarily unavailable" (no LLM key, as the user chose)

## Known limits
- Live order placement has not been tested with real keys
- Some exchanges reject fake secrets with a decode error before the auth call; this is still shown as "Exchange verification failed: …"
- Binance.com may be geo-blocked from the server (HTTP 451)

## Backlog
- P1: Connect an LLM for Luchii chat and help when the user wants it
- P2: Check the exchange minimum order size before placing live orders
