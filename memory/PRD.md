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

## Implemented (2026-10-15, iteration 11 — "boost": aggressive multi-market scalping)
- User-approved boost applied to ALL bots (migration db.meta boost_v1): `aggressive` (SMA 5/13, RSI cap 80, trend filter = price above SMA13 only, volume filter skipped), `size_pct` 50 (% of free quote balance per trade; 0 = fixed position_usd), TP +0.6% / SL −0.4% (trailing + partial TP still apply), `assets` [BTC, ETH, SOL]. No fee guard (user choice).
- market.py fetches Coinbase ticker+candles for BTC/ETH/SOL → SNAPSHOT.markets. engine generalised per asset (pos_of/pos_fields/mkt; ETH/SOL positions in bot.alt_positions.<ASSET>); execute() resolves <ASSET>/<quote> per exchange, checks min amount & min cost; sync_position covers all assets; total_usd counts ETH/SOL holdings.
- UI: CONFIG tab has MARKETS toggles, AGGRESSIVE toggle, % of balance input, risk warning; filter labels reflect aggressive mode; status card shows alt positions and %/TRADE.
- iteration_9: 14 new + 46 regression tests pass; UI verified 1920/390. Testing-agent minors fixed (mkt() no BTC fallback for alts, % size used for auto-fund amount, per-asset log pct, pending size).

## Implemented (2026-10-16, iteration 12 — LUCHII_003 production strategy)
- New `backend/strategy.py`: EMA20/50/200, ATR14 + 20-bar ATR mean, ADX14, SMA5/13, trend_pct, volume_ratio; regime BULL/BEAR/CHOP (EMA50 vs EMA200 + ADX; chop = no trades); entry score (5×20 pts) ≥80 + ADX>25 + ATR expansion; BTC master filter + BTC/alt BEAR block for ETH/SOL; risk-based sizing (1.5% score≥90, 1% ≥80, BEAR cap 0.75%) with ATR×2.5 stop; tiered TP 25% at +1/+2.5/+5%; staged stop (break-even +0.5%, lock +1%, peak−3ATR at +2%, peak−2ATR at +5%). Spot only — no shorts.
- Engine: `bot.strategy` = luchii003 (default for ALL users via db.meta luchii003_v1) | aggressive (yesterday's scalp kept as option). Positions carry stop/tiers/entry_atr (BTC root, alts in alt_positions). Tier slices below exchange minimum are rounded up or skipped (never dump the position). market.py keeps 300 1-min candles.
- API `GET /api/strategy/metrics`; dashboard SignalScoreCard (score bar, regime, trend, vol, ADX, RSI, expected move, stop, reason); CONFIG tab STRATEGY switch, aggressive-only controls hidden under LUCHII_003.
- iteration_10: 22 new + 60 regression tests pass; UI verified 1920/390; agent findings fixed.
- Out of scope (user chose A only): the `luchii-backtest` package / walk-forward / Monte Carlo; governance/ops docs pasted by user are not app features.

## Implemented (2026-10-17, iteration 13 — gates, hard entry filters, win stats, backtest package)
- `backend/gates.py`: rolling 30-day stats from bot_trades/bot_pnl (PF, win rate, avg gain/loss, max DD) per asset and per strategy; gate state machine OPEN/WARNING/RESTRICTED/DISABLED with hysteresis (3 fails to close, 7 passes PF>1.35/DD<8/100+ trades to reopen, DISABLED sticky) — gates NEW ENTRIES ONLY, exits always run; <50 trades = OPEN by default; audit_log event `strategy_gate_changed`. Kill switch (3 consecutive losses, daily loss >3%, weekly DD >8%). Risk multiplier by rolling PF (0.25/0.5/1/1.5×). SOL longs disabled until SOL rolling PF>1.10 over 50+ trades.
- `engine.entry_guard` hard filters for every long (both strategies): ADX≥25, RSI<70, volume ratio≥1.2, score≥70, confidence≥70%, and for ETH/SOL BTC>EMA200 + BTC RSI>50 + BTC trend>0.
- API: GET /api/strategy/gate, GET /api/strategy/stats. Dashboard StrategyHealthCard (gate badge, PF/win/avg+/DD, per-market rows, gate timeline).
- `backend/luchii_backtest/` (package): Coinbase 1m history fetch, LUCHII_003 simulation with fees/slippage/gap stop & TP handling, profit factor/Sharpe/drawdown, grid walk-forward with stability/overfit flags, seeded Monte Carlo, deployment_gate(). `python -m luchii_backtest.run BTC 3000`. tests/test_luchii_backtest.py (22 tests).
- First real run (BTC, last 1500 bars): 2 trades, both losers, PF 0 — too little data for walk-forward (needs ≥2500 bars). No profitability claim is made.
- Not built (org tooling, out of app scope): Jira CSV, Docker/Alembic, Maven/Java SDK, PagerDuty, CI yaml.

- Testing-agent iteration_11 fixes: loss kill switch now windowed (last 24h) with a 4h cooldown instead of permanent; gates/kill switch/SOL rule also apply to manual-confirm BUYs; zero-equity pnl points skipped (and no longer inserted); backtest pnl includes entry fee, stop fills always close full position, Monte Carlo uses per-trade pnl; run.py defaults 6000 bars / train 1500 / test 500. Latest BTC run log: /app/memory/backtest_btc_latest.log.

## Known limits
- Live order placement has not been tested with real keys
- Some exchanges reject fake secrets with a decode error before the auth call; this is still shown as "Exchange verification failed: …"
- Binance.com may be geo-blocked from the server (HTTP 451)
- SOLANA_RPC_URL is the public mainnet RPC (rate-limited); a dedicated RPC is advisable for production. The user still needs to pick the two intended token mints via search (several SAINDRA/Dacity mints exist).

## Backlog
- P1: Connect an LLM for Luchii chat and help when the user wants it
- P2: Dedicated Solana RPC; TP/SL for Solana token positions themselves (currently they follow the BTC signal)


## 2026-06 · Phase 1 of three-layer architecture (tested: iteration_12, 22/22 + regression)
- Regime engine (strategy.regime): TREND_BULL / TREND_BEAR / BREAKOUT / CHOP / MEAN_REVERSION / PANIC. CHOP + MEAN_REVERSION = no new trades (both strategies). BREAKDOWN (new low) = no longs. PANIC = 0.5x risk.
- Gate rules: OPEN PF>1.10 & DD<8; WARNING PF<1.10; RESTRICTED PF<1.0 or DD>8 (hysteresis); DISABLED PF<0.8 or DD>15 or 10 losses in a row; MIN_TRADES 30. DISABLED sticky; POST /api/strategy/gate/reset (owner) re-opens, restarts rolling stats from reset time, audit_log + Telegram.
- Symbol kill switch (db.symbol_gates): PF<0.9 over 20+ trades auto-disables symbol; SOL disabled by policy by default; GET /api/strategy/symbols, POST /api/strategy/symbols/{BTC|ETH|SOL} {enabled}. Owner re-enable restarts stats from that time.
- Telegram alerts: gate state changes, symbol disable/enable, kill switch (once per 4h).
- Trade audit (db.trade_audit): full schema incl. score/adx/rsi/vol/btc state/regime/entry+exit reason/MAE/MFE/fees/slippage/net. GET /api/trades/audit, /api/trades/audit.csv, /api/trades/buckets (score/ADX/regime/symbol buckets).
- StrategyHealthCard: CSV export, per-symbol ON/OFF toggles, owner re-enable button when DISABLED.
- Backlog (Phase 2/3): 30-day walk-forward (train20/val5/test5 sliding), 5000-run Monte Carlo with probability of ruin, multi-agent voting, reinforcement ranking, dynamic symbol rotation. Profitability is NOT guaranteed; no live edge proven yet.


## 2026-06 · Phase 2/3 validation + agent voting
- audit.py: Mongo indexes (trade_id unique, user+symbol+status, strategy+entry_time, regime, trade_result, score) + validate() integrity checks (direction, qty>0, position_usd>0, exit>=entry, trade_result WIN/LOSS/BREAKEVEN).
- agents.py: momentum/trend/breakout/volume/structure agents score 0-100; weighted final >= 75 and regime permission (REGIME_ALLOWED) required for a LUCHII_003 long. Reinforcement weights from audit (agent PF, >=10 trades) refreshed every 10 min per bot. winner_agent/agent_scores stored in trade_audit. GET /api/strategy/agents.
- luchii_backtest: indicator_series cache (700x faster), day_windows/walk_forward_days (train20/val5/test5 sliding, train PF>=1.10, val PF>=1.0), approve_walk_forward (avg PF>=1.10, DD<=15, WR>=35, expectancy>0, >=5 windows), monte_carlo_ruin (5000 bootstrap; ruin<5%, DD95<20%, expectancy>0).
- Endpoints: GET /api/strategy/montecarlo (live audit trades), POST/GET /api/strategy/walkforward/{asset}?days=30 (background job, result in db.backtests).
- ValidationCard on dashboard: agent votes, Monte Carlo, run/view walk-forward.
- Fix (iteration_13 HIGH): backtests no longer mutate live strategy.RULES — thread-local override via strategy.rules()/set_rules_override.
- Tested: iteration_13 31/31 new tests, full regression 259/260 (1 order-dependent flaky test in iteration9, passes alone).
- Honest 30-day BTC result (Coinbase 1-min, 43,500 candles): walk-forward 1 window skipped (train PF < 1.10), full-period 130 trades, expectancy -$22/trade, DD95 36% → REJECTED. LUCHII_003 has NO proven edge on 1-min BTC yet.
- 2026-06 FIX (critical, user-reported "bot never trades"): engine.tick_bot treated entry_blocked()'s (None, gate) tuple as truthy → every BUY was swallowed as a reason-less WAITING log since the gate refactor. Fixed to check blocked[0]; regression test tests/test_iteration14_buy_not_swallowed.py (fails on old code). Live OKX path proven with a real $5.50 test order on the owner account (both fills closed).
- Aggressive strategy now bypasses regime/score/ADX/BTC/agent filters (owner choice "b"); kill switch, strategy gate and symbol kill switch still apply.
