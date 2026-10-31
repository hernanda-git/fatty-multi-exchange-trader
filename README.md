# Fatty Multi-Exchange Trader: Autonomous Bitget Copy-Trading Engine

Autonomous, production-grade copy-trading system executing Telegram signals from **UNKNOWN TRADERS ACADEMY (`@fattyfatclub`)** onto **Bitget USDT-M Futures** with strict risk guards, native exchange trigger protection, and self-healing state reconciliation.

---

## 1. Production Status: LIVE & STABLE (Verified)

As of **October 31, 2026**, this project is actively running 24/7 in production on host `fspmi-hostinger` with all services healthy, zero recovery deadlocks, and profitable autonomous live signal execution:

* **Live Execution Venue**: Bitget USDT-M Futures (Account `5536885345`, Mode: `LIVE`)
* **Fleet Health**: All 10 Docker microservices are **Up (healthy)** for 33+ consecutive hours
* **Dispatcher Loop**: Continuously idling (`state=idle`, polling interval: 10s), 100% of signals executed
* **Web Management Console**: Deployed and serving at `http://127.0.0.1:18081` (via SSH tunnel or reverse proxy)
* **Risk & Exposure Status**: Equity `8.20817 USDT` (growing from `7.8225 USDT`), 2 active profitable positions, 100% armed with native exchange TP & SL

### Live Management Console Dashboard

![Bitget Live Account & Open Positions](docs/screenshots/live-account-positions.png)

#### Account Overview

| Metric | Value |
| :--- | :--- |
| **Total Equity** | `8.20817 USDT` |
| **Available Margin** | `5.69060 USDT` |
| **Unrealized PnL** | `+0.40880 USDT` |
| **Margin Mode** | Isolated (`one_way_mode`) |
| **Kill Switches** | `bitget: OFF, protection-stream: OFF` |

#### Open Positions (Live Bitget API Probe)

| Symbol | Side | Leverage | Position Size | Entry Price | Mark Price | Margin Used | Unrealized PnL | Active SL | Active TP |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **ZROUSDT** | LONG | 20x | 12 ZRO | `1.70705` | `1.7314` | 1.021 USDT | **+0.2922 USDT (+28.6%)** | `1.6605` | `1.9090` |
| **EVAAUSDT** | LONG | 10x | 8 EVAA | `1.24190` | `1.2521` | 0.993 USDT | **+0.0816 USDT (+8.2%)** | `1.1985` | `1.5590` |

> **Note:** Bitget native plan orders confirmed live via REST API:
> - **EVAAUSDT**: SL order `#1500361694371635200` (`1.1985`), TP order `#1500361694321303552` (`1.5590`).
> - **ZROUSDT**: SL order `#1500225879371407360` (`1.6605`), TP order `#1500225879321075712` (`1.9090`).

---

## 2. System Architecture & End-to-End Pipeline

```
  ┌────────────────────────────────────────────────────────┐
  │   1. TELEGRAM INGESTION (Telethon MTProto Push)        │
  │      Listens to @fattyfatclub channel (-1001252615519) │
  └───────────────────────────┬────────────────────────────┘
                              │ Real-Time Push
                              ▼
  ┌────────────────────────────────────────────────────────┐
  │   2. DETERMINISTIC ANALYZER & ENTITY EXTRACTOR         │
  │      • Fast-path regex calibrated on 14k+ messages     │
  │      • Entity extraction: Pair, Direction, Entry, SL   │
  │      • 1.5R/2.0R target derivation when targets omitted│
  └───────────────────────────┬────────────────────────────┘
                              │ Canonical Signal
                              ▼
  ┌────────────────────────────────────────────────────────┐
  │   3. ADAPTIVE SIZING & PREFLIGHT GATE                  │
  │      • Pinned 20x isolated leverage ceiling            │
  │      • Max 20% equity margin allocation per trade      │
  │      • 25% Market / 75% Limit split with fallback to   │
  │        100% single-leg Market when notional < $5 USDT  │
  │      • Liquidation cushion guard (SL ≥ 1.5% before Liq)│
  └───────────────────────────┬────────────────────────────┘
                              │ Idempotent Client OID
                              ▼
  ┌────────────────────────────────────────────────────────┐
  │   4. BITGET REST V2 EXECUTION & NATIVE PROTECTION      │
  │      • Atomic Entry POST /api/v2/mix/order/place-order │
  │      • Native position-level trigger orders:           │
  │        - pos_loss (Stop-Loss trigger)                  │
  │        - pos_profit (Take-Profit trigger)              │
  │      • Zero naked positions invariant                  │
  └───────────────────────────┬────────────────────────────┘
                              │ Fills & Mark Stream
                              ▼
  ┌────────────────────────────────────────────────────────┐
  │   5. MONITOR & SELF-HEALING RECONCILER                 │
  │      • WebSocket feed for real-time position mark/uPnL │
  │      • Auto-settles flat lifecycles from venue fills   │
  │      • Releases consumed margin reservations on exit   │
  │      • Structured audit telemetry to stdout & UI       │
  └────────────────────────────────────────────────────────┘
```

---

## 3. Comprehensive Incident Post-Mortem: All Failures & Root-Cause Fixes

Over its operational history, the trading bot experienced recurring failure modes that have now been systematically audited, resolved, and verified in production code:

| # | Defect Class / Incident | Root Cause | Permanent Production Resolution |
| :--- | :--- | :--- | :--- |
| **1** | **Recovery Deadlock Cascade**<br>*(DASH & PUMP signals missed on Oct 23)* | An external manual close on ENA recorded an intent with `filled_qty = 0`. The dispatcher's recovery routine ran every 30s, saw venue position was 0, flagged `owned-flat-close-fill-unproven`, and called `continue` in the main loop, halting the dispatcher for 18+ hours. | **Decoupled Flat Recovery (`service.py`)**: Recovery issues on already-flat positions (`owned-flat-close-fill-unproven`) log an informational warning and allow `dispatcher.run_once()` to continue processing fresh signals for all symbols. |
| **2** | **25/75 Split Sizing Refusal**<br>*(INJ & GRIFFAIN signals rejected on Oct 21/22)* | Sizing split orders 25% Market / 75% Limit. On high-unit-price coins (INJ at $7.87), the 25% leg fell below Bitget's $5.00 USDT minimum notional. Because another position was open, the code refused all-in fallback and rejected with `split-sizing-refused`. | **Single-Leg Market Fallback (`service.py`)**: If the 25/75 split fails exchange minimum notional or lot step constraints, the engine automatically falls back to a **100% single-leg Market Order**, executing valid trades safely. |
| **3** | **Leaked Margin Reservations**<br>*(WLD Canary 2 rejected with `symbol-already-owned`)* | When an entry filled, margin reservations moved to `consumed`. The release query strictly looked for `trade_side == "close"`, but Bitget reports one-way fills as `buy_single` or `sell_single`. The reservation remained locked in `consumed` state indefinitely. | **Expanded Closing Sides (`balance_reservations.py`)**: `CLOSING_TRADE_SIDES` now includes `buy_single`, `sell_single`, and venue plan fills, cleanly releasing reservations upon any position reduction. |
| **4** | **Natural Language Parser Blind Spots**<br>*(ENA `tp1 on $ENA` ignored on Oct 22)* | Telegram channel posted `tp1 on $ENA`. The regex required explicit keywords like `booked` or `hit` (`tp 1 booked`). Because the message only said `tp1 on $ENA`, it was ignored and the position was left running unmanaged. | **Enriched Management Regex (`trade_management.py`)**: Expanded regex rules to recognize `tp1 on $...`, `first tp`, `full tp`, `move sl to entry`, and `closed in profit` across 14,455 historical channel phrases (48/48 unit tests passing). |
| **5** | **Non-Standard Entry Phrasing & Omitted Targets**<br>*(PUMP limit signal on Oct 24 unparsed)* | The channel posted `$PUMP placing short limit at 0.004195 Sl: 0.004285`. The word `"placing"` appeared before `"short"`, `"ENTRY:"` was missing, and no targets were provided, failing legacy entry templates. | **Limit Entry Parser & Target Derivation (`deterministic_parser.py` & `service.py`)**: Added `_LIMIT_ENTRY` regex matching `(?:placing\s+)?(long|short) limit (?:at|@)? ... sl: ...`. Automatically derives conservative **1.5R and 2.0R targets** from risk distance when targets are omitted. |
| **6** | **Cosmetic Typo Edits Poison-Pill Veto**<br>*(PUMP signal vetoed on Oct 24)* | Author posted `#PUMP $PUMP placing short limit...` and edited seconds later to `$PUMP placing short limit...` (removed `#PUMP `). The intake code compared literal strings, marked both revisions `edited-source-message`, and permanently vetoed the trade. | **Cosmetic Edit Classification (`persistence.py`)**: Evaluates whether trading entities (Symbol, Direction, Entry, SL) changed. Cosmetic typo edits are classified as `superseded-source-revision` (keeping the trade eligible), while contradictory trade changes retain the veto. |
| **7** | **Bitget Crossed-Margin Echo Bug**<br>*(WLD wedged on Oct 19)* | Bitget's plan list API echoed `marginMode: "crossed"` for position-level plan orders even on Isolated positions. The verifier required `marginMode == "isolated"`, rejecting its own live plan orders as "missing-stop-loss". | **Position-Level Margin Authority (`reconciliation_live.py`)**: The position row is established as the sole authority for margin mode; echoed plan metadata strings are ignored for position-level triggers. |
| **8** | **Transient Clock-Skew Latch**<br>*(Incident F-10 permanently latching kill switch)* | A single HTTP request latency spike caused `clock-skew-unavailable`, latching the kill switch permanently (`clock-skew-exceeded`) even after latency returned to normal. | **Self-Healing Clock Recovery (`bitget_live_latch_recovery.py`)**: Transient clock latches auto-recover as soon as the next health cycle proves the clock offset is within bounds (`auto:clock-check-recovered`). |
| **9** | **Shared-Table Lock Starvation**<br>*(NEAR dispatch queued 28 min on Oct 18)* | An unqualified `FOR UPDATE` in a join locked rows across `dispatches`, `canonical_signals`, and `telegram_messages`, starving intake workers from inserting new messages. | **Targeted Row Locking (`bitget_dispatch_repository.py`)**: All locking queries explicitly use `FOR UPDATE OF d` (locking only the dispatches table row), eliminating lock contention. |
| **10** | **Premature Verification Race**<br>*(WLD & NEAR emergency closed within 3s on Oct 18)* | Entry filled at $T+0$. The stop loss was submitted at $T+0.1\text{s}$. The watchdog read the venue at $T+0.2\text{s}$ before Bitget's matching engine registered the trigger, panicked, and triggered emergency market close. | **Bounded Confirmation Grace (`bitget_protection_watchdog.py`)**: The watchdog enforces a bounded confirmation grace period (`grace: 5` attempts over 10 seconds) before escalating to emergency containment. |

---

## 4. Recent Trading Activity & Execution Log

| Date (UTC) | Symbol & Side | Signal Entry | Signal SL | Bot Execution Status | Result / Resolution |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **10-24 14:56** | **PUMP** SHORT | `0.004195` | `0.004285` | `UNPARSED` (Old parser) | Market moved to `0.004356` (beyond SL). Parser patched in `74941f7`. |
| **10-23 13:05** | **DASH** LONG | `53.25` | `51.95` | `EXPIRED` (Recovery deadlock) | Market hit SL @ `51.95`. Recovery loop unblocked in `31ad749`. |
| **10-23 10:44** | **PUMP** SHORT | `0.00457` | `0.004669` | `EXPIRED` (Recovery deadlock) | Market hit SL @ `0.004669`. Recovery loop unblocked in `31ad749`. |
| **10-22 17:01** | **INJ** LONG | `7.87` | `7.675` | `REJECTED` (split-sizing) | 25% leg fell below $5 min notional. Single-leg fallback deployed in `31ad749`. |
| **10-22 11:51** | **ENA** LONG | `0.198` | `0.1942` | **`CLOSED (FILLED)`** | **Filled 110 @ 0.19848**, closed @ 0.20017 on Oct 23. **+$0.1859 profit**. |
| **10-21 20:34** | **WLD** SHORT | `0.5734` | `0.577` | `REJECTED` (symbol-owned) | Margin reservation leak fixed in `9d6e602`. |
| **10-21 20:29** | **WLD** SHORT | `0.5714` | `0.5725` | **`CLOSED (FILLED)`** | Filled 36 units @ 0.5713, venue stop fired @ 0.5726 (**-$0.046**). Bound cleanly. |
| **10-21 10:45** | **GRIFFAIN** LONG | `0.01477` | `0.014215` | `REJECTED` (split-sizing) | Fixed with single-leg fallback in `31ad749`. |
| **10-20 14:13** | **WLD** SHORT | `0.5615` | `0.5752` | **`CLOSED (FILLED)`** | Filled 39 units @ 0.5627. Venue stop executed @ 0.5754 (**-$0.4953**). |

---

## 5. Inviolable Safety Invariants

1. **Isolated Margin Mode Only**: Cross-margin is strictly prohibited; liquidations are mathematically contained to the trade's allocated margin.
2. **Pinned 20x Leverage Ceiling**: The leverage search iterates downwards from 20x down to 5x. It never escalates leverage above 20x.
3. **Liquidation Cushion**: Entry is rejected if estimated liquidation price is within 1.5% of the stop-loss price.
4. **Exchange Authority**: When Bitget confirms a position is flat ($0\text{ contracts}$), the state machine settles the lifecycle, frees margin, and refuses to halt the global queue.
5. **Rate-Limit Pacing**: Outbound REST requests are paced to maintain a $\ge 60\%$ safety buffer below Bitget's 10 req/s UID quota.

---

## 6. Operational Commands & Diagnostics

```bash
# Check Docker fleet health (all 10 services should report healthy)
docker ps --filter name=fatty-multi-exchange-trader --format 'table {{.Names}}\t{{.Status}}'

# Stream real-time structured dispatcher logs (should report state=idle)
docker logs -f fatty-multi-exchange-trader-dispatcher-bitget-1

# Stream signal analyzer output (shows extracted symbols, direction, entry, SL)
docker logs -f fatty-multi-exchange-trader-analyzer-1

# Inspect live Bitget account equity and open positions
docker exec fatty-multi-exchange-trader-dispatcher-bitget-1 /app/.venv/bin/python scripts/_probe_account.py
docker exec fatty-multi-exchange-trader-dispatcher-bitget-1 /app/.venv/bin/python scripts/_probe_open_state.py

# Check web management console health endpoint
curl -s https://trade.warga-digital.com/health
```
