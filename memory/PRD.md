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

## Implemented (2026-10-15, iteration 6 — strategy controls + Solana DEX)
- Bot tick is now 5s (BOT_TICK_SECONDS=5). Per-bot take_profit_pct (default 1.5), stop_loss_pct (default 1.0), trend_filter (BUY only when price is above a rising SMA25) and volume_filter (BUY only when the last closed 1-min candle's volume is above the 20-bar average). TP/SL exits fire on any tick while in position. Config tab exposes all four.
- Solana DEX (Jupiter lite-api, mainnet): per-user private key imported in Settings → SOLANA DEX, Fernet-encrypted in db.solana_wallets, never returned. Token search (Jupiter), up to 5 mints, SOL-per-buy, slippage, enable toggle. Every bot BUY/SELL fill mirrors into SOL→token / token→SOL swaps (non-blocking create_task); manual BUY/SELL buttons per token. Balances read legacy SPL + Token-2022. Swaps are logged in the execution log and db.solana_swaps. Insufficient SOL fails before broadcast.
- No win-rate guarantee is made or implied; the controls above are risk management, not a 100% win rate.
- iteration_6: 22/22 backend, all frontend flows (desktop + 390px) passed; test fixture teardown fixed for xdist.

## Implemented (2026-10-15, iteration 7 — token TP/SL + Helius RPC)
- SOLANA_RPC_URL now points at the user's private Helius endpoint (in backend/.env only).
- Per-token take-profit/stop-loss: wallet defaults token_tp_pct 25 / token_sl_pct 15, per-mint overrides (token_cfg.<mint>.tp_pct/.sl_pct, merged per field, DELETE /api/solana/config/token/{mint} resets). Each SOL→token buy records positions.<mint> {entry_usd, raw}; solana_dex.watcher() checks Jupiter price/v3 every 5s and sells the token back to SOL on a hit, independent of the BTC bot; failed sells back off 2 min. UI shows entry, move %, TP/SL per token, override and "use defaults".
- iteration_7: 13/13 new + 22/22 regression backend tests, all frontend flows passed.

## Implemented (2026-10-15, iteration 8 — trailing stop)
- BTC bot: `trailing_stop` (default on) + `peak_price` tracked every tick while in position; stop-loss % is measured from the peak once price is above entry ("TRAILING STOP hit … locked +x%"), fixed stop from entry otherwise / when off. Config tab toggle.
- Solana tokens: wallet `trailing_stop` (default on) + `positions.<mint>.peak_usd` updated by the watcher; same peak-based rule; token cards show peak. Verified by unit checks on engine.signal, API round-trips and a simulated watcher position; 35/35 regression tests pass.

## Implemented (2026-10-15, iteration 9 — partial take profit + built-in mints)
- Built-in default tokens for every Solana wallet: SAINDRA A2yA6AFtfKkfVkfsnGpvsKoUV5kQPpMYboZsBPvmkA9T and Dacity nxPqZ4F7ux8Vph5NFu25rEP6Kx9Gr1U6Fmj36Rjpump (set on import, auto-added when a wallet has no tokens).
- Partial take profit (`partial_tp`, default on, toggle in CONFIG and SOLANA DEX): first TP hit sells half (BTC: falls back to full sell if half < exchange minimum; `tp_taken` flag prevents repeat), remainder rides the trailing stop. Solana positions keep raw/tp_taken after the half sell; card shows "½ TAKEN".
- Verified via signal unit checks, config round-trips, default-token import, simulated watcher position; 35/35 regression.

## Implemented (2026-10-15, iteration 10 — exit-rule fix + Fund Wallet Guide)
- Root cause of "40% win rate / no gains": the in-position exit rule (SELL on SMA7<SMA25 or RSI>75) closed $1 trades on tiny reversals before TP/trailing could act (2W/3L, net ≈ 0 after fees). Fix: SMA cross exit only cuts losers beyond −0.3%; winners HOLD and ride partial TP / trailing stop; RSI>75 no longer exits. TP comparison has epsilon; pnl/realized_pnl kept at 4 dp.
- SOLANA DEX tab: "FUND THIS WALLET" guide with QR (solana:<pubkey>), full address, copy button, network warning.
- iteration_8 report: 11 new + 35 regression tests pass, frontend flows pass at 1920/390.
- Honest note: win rate depends on the market; with $1 positions exchange fees (~0.1%/side) dominate — larger position size needed for meaningful gains.

## Known limits
- Live order placement has not been tested with real keys
- Some exchanges reject fake secrets with a decode error before the auth call; this is still shown as "Exchange verification failed: …"
- Binance.com may be geo-blocked from the server (HTTP 451)
- SOLANA_RPC_URL is the public mainnet RPC (rate-limited); a dedicated RPC is advisable for production. The user still needs to pick the two intended token mints via search (several SAINDRA/Dacity mints exist).

## Backlog
- P1: Connect an LLM for Luchii chat and help when the user wants it
- P2: Dedicated Solana RPC; TP/SL for Solana token positions themselves (currently they follow the BTC signal)
