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

## Implemented (2026-10-15, iteration 2)
- OKX now finds the right region automatically (global, US, EEA). The user's key is an OKX US key (us.okx.com). OKX error codes now show plain-language messages.
- The app reads each key's permissions (read/trade/withdraw/transfer) and warns when Trade or Withdraw is missing.
- Exchange tab: account sync (trading and funding balances) and manual market BUY/SELL.
- Wallet tab: balances per account, deposit address per coin and network, funding⇄trading transfer, withdraw with network, fee and memo, and deposit/withdrawal history.
- Self-custody wallets show real balances (mempool.space / public ETH RPC) and offer SEND TO EXCHANGE, which the user signs in OKX Wallet, MetaMask or Coinbase Wallet.

- Engine now checks the balance and minimum order size before each order (one throttled WAITING log line instead of rejected spam), syncs BTC held on the exchange as a position, and reads the real average fill price back from OKX after each order.
- First real OKX fill confirmed: SELL 0.000111 BTC/USDT @ 80,658.90 (order 4012130822437031936).
- P&L chart starts from the real exchange balance (start_equity/start_ts on the bot) instead of $10,000. get_bot creates the bot with an upsert, so parallel first requests no longer cause duplicate-key 500s.

## Known limits
- Live order placement has not been tested with real keys
- Some exchanges reject fake secrets with a decode error before the auth call; this is still shown as "Exchange verification failed: …"
- Binance.com may be geo-blocked from the server (HTTP 451)

## Backlog
- P1: Connect an LLM for Luchii chat and help when the user wants it
- P2: Check the exchange minimum order size before placing live orders
