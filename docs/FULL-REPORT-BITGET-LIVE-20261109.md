# Fatty Multi-Exchange Trader (Bitget Live) — Full System & Operational Report

**Date**: 2026-11-09  
**Host Environment**: VPS `srv691444.hstgr.cloud` (`46.202.155.192`)  
**Deployment Base**: `/home/valarion/apps/fatty-multi-exchange-trader`  
**Latest Git Revision**: `916fd97` (fix(analyzer): robust image/text parsing, price scaling alignment, and deterministic fallback)

---

## 1. Executive Status

| Dimension | Evidence / State |
| :--- | :--- |
| **System Status** | `🟢 ONLINE` · 10 of 10 Docker services healthy and active |
| **Venue Connectivity** | Bitget `USDT-FUTURES` authenticated API responding (GET `/api/v2/mix/account/account` OK) |
| **Kill Switches** | `✅ INACTIVE` · Global and protection-stream latches released |
| **Account Equity** | **11.042558 USDT** (Available: 11.042558 USDT · Margin Used: 0 USDT) |
| **Open Positions** | **0** (Venue REST confirmed flat: 0 positions) |
| **Open Orders** | **0** (Venue REST confirmed: 0 open orders) |
| **Reconciliation Drift** | **0** (`DB 0 = Provider 0`) |
| **Active Code Branch** | `main` on commit `916fd97` |

---

## 2. Infrastructure & Service Topology

The system operates across 10 coordinated containers on the Hostinger VPS:

```
[Telegram Channel]
        │
        ▼ (polling & media sync)
[intake]
        │
        ▼ (text / OCR image analysis)
[analyzer] ── (Codex / Regex Fallback)
        │
        ▼ (canonical signal)
[dispatcher-bitget] ── (sizing & venue admission)
        │
        ├──▶ [Bitget REST / WS] (REST orders + native TP/SL)
        │
        ▼ (position updates & native orders)
[monitor-bitget] ◀──▶ [source-management] (tracks 'sl to entry', manual closes)
        │
        ├──▶ [notification-sender] ──▶ [Operator Telegram Alerts]
        │
[operator-bot] & [web / console-worker] ──▶ (Admin control on :18081)
```

### Container Status Table

| Container | Health | CPU % | Memory | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `fatty-multi-exchange-trader-analyzer-1` | healthy | ~32% | 170 MiB | Extracts signals from raw text or chart screenshots. |
| `fatty-multi-exchange-trader-dispatcher-bitget-1` | healthy | 0.0% | 172 MiB | Enforces margin caps, preflight checks, places entries & native stops. |
| `fatty-multi-exchange-trader-source-management-1` | healthy | 0.0% | 180 MiB | Handles channel follow-up messages (`tp1 booked`, `sl to entry`). |
| `fatty-multi-exchange-trader-monitor-bitget-1` | healthy | 0.5% | 246 MiB | Real-time position watchdog via Bitget WS and REST. |
| `fatty-multi-exchange-trader-intake-1` | healthy | 0.1% | 383 MiB | Telegram MTProto client capturing incoming posts. |
| `fatty-multi-exchange-trader-notification-sender-1` | healthy | 0.1% | 184 MiB | Outbox queue delivering HTML telemetry to Telegram. |
| `fatty-multi-exchange-trader-operator-bot-1` | healthy | 0.0% | 390 MiB | Interactive command bot (`/health`, `/protection`, `/pause`). |
| `fatty-multi-exchange-trader-console-worker-1` | healthy | 13.5% | 397 MiB | Background console runner & backup manager. |
| `fatty-multi-exchange-trader-web-1` | healthy | 0.2% | 125 MiB | Private Web UI & REST API (`http://127.0.0.1:18081`). |
| `fatty-multi-exchange-trader-postgres-1` | healthy | 30.0% | 1.41 GiB | PostgreSQL 16 database storing the immutable ledger. |

---

## 3. Financial Performance & Trading History

### Historical Performance (64 Fills to Date)
- **Gross Realized Profit**: `+8.630892 USDT`
- **Gross Realized Loss**: `-8.604459 USDT`
- **Total Fees Paid**: `0.612925 USDT`
- **Net Realized PnL**: `🔴 -0.586492 USDT`
- **Current Account Equity**: `11.042558 USDT`

### Detailed Fills (October 25 – November 9, 2026)

| Filled At (UTC) | Symbol | Order ID / Role | Side | Price | Qty | Fee (USDT) | Realized PnL |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 2026-11-07 09:51:49 | `LITUSDT` | `...-native-close` | SELL | 3.323 | 4 | 0.007975 | `-0.6280` |
| 2026-11-07 02:27:00 | `LITUSDT` | `...-entry` | BUY | 3.48 | 4 | 0.008352 | `0.0000` |
| 2026-11-05 10:49:31 | `APEUSDT` | `source-management-close` | SELL | 0.1164 | 189.8 | 0.013256 | `-0.0569` |
| 2026-11-04 19:03:24 | `APEUSDT` | `...-entry` | BUY | 0.1167 | 189.8 | 0.013290 | `0.0000` |
| 2026-11-03 14:35:53 | `SNDKUSDT` | `...-native-close` | BUY | 1145.74 | 0.019 | 0.013061 | `-0.4569` |
| 2026-11-03 12:28:19 | `SNDKUSDT` | `...-entry` | SELL | 1121.69 | 0.019 | 0.012787 | `0.0000` |
| 2026-11-02 17:49:28 | `ENAUSDT` | `source-management-close` | SELL | 0.16802 | 128 | 0.012904 | `-0.4083` |
| 2026-11-02 17:09:32 | `ENAUSDT` | `...-entry` | BUY | 0.17121 | 128 | 0.013149 | `0.0000` |
| 2026-11-01 13:36:30 | `ZROUSDT` | `...-native-close` | SELL | 1.9075 | 12 | 0.013734 | **`+2.4054`** |
| 2026-11-01 07:07:58 | `EVAAUSDT` | `...-native-close` | SELL | 1.5685 | 8 | 0.007529 | **`+2.6128`** |
| 2026-10-31 09:47:01 | `EVAAUSDT` | `...-entry` | BUY | 1.2419 | 8 | 0.005961 | `0.0000` |
| 2026-10-31 00:47:22 | `ZROUSDT` | `...-entry` | BUY | 1.7071 | 12 | 0.006146 | `0.0000` |
| 2026-10-30 18:18:29 | `PUMPUSDT` | `...-native-close` | BUY | 0.003974 | 5476 | 0.013057 | `-0.0821` |
| 2026-10-30 13:54:04 | `PUMPUSDT` | `...-entry` | SELL | 0.003989 | 5476 | 0.006568 | `0.0000` |
| 2026-10-27 18:38:54 | `POWERUSDT` | `source-management-close` | SELL | 0.08097 | 193 | 0.009376 | `-0.0540` |
| 2026-10-27 09:38:44 | `POWERUSDT` | `...-entry` | BUY | 0.08125 | 193 | 0.009409 | `0.0000` |
| 2026-10-27 09:15:37 | `ENAUSDT` | `...-native-close` | SELL | 0.20243 | 106 | 0.012875 | `-0.4723` |
| 2026-10-27 08:12:38 | `ENAUSDT` | `...-entry` | BUY | 0.20689 | 106 | 0.013158 | `0.0000` |
| 2026-10-26 14:19:21 | `WLDUSDT` | `...-native-close` | BUY | 0.4407 | 24 | 0.006346 | `+0.0006` |
| 2026-10-26 13:44:31 | `ONDOUSDT` | `operator-close` | BUY | 0.4133 | 13.3 | 0.003298 | `+0.0053` |
| 2026-10-26 13:23:53 | `PUMPUSDT` | `operator-close` | BUY | 0.00423 | 5164 | 0.013106 | `-0.1756` |
| 2026-10-26 09:12:04 | `WLDUSDT` | `source-management-close` | BUY | 0.4393 | 24 | 0.006326 | `+0.0342` |

---

## 4. Key Architectural & Reliability Milestones

### 1. Robust Range & Image Parsing Fallback (Commit `916fd97`, 2026-11-09)
- **Problem**: Message #16468 (`$PEPE short limit around 0.51 - 0.513`) failed image parsing because the vision output returned range strings (`0.51–0.513`) and chart-scale coordinates (`0.46913e-5`). The message was marked as media-bearing, which bypassed deterministic text parsing.
- **Solution**: Added decimal extraction that handles ranges and en-dashes; dynamically aligns target scale to entry scale; added automatic fallback to `parse_explicit_signal(raw_text)` if image extraction yields `None`.
- **Status**: Verified by unit test suite and running in live production.

### 2. Market Entry Forcing on Natural Signals (Commit `1022601`, 2026-11-06)
- Natural language declarations (`"longing here"`, `"shorting here"`) are automatically assigned 100% market order allocation to prevent missed fills on immediate price breakouts.

### 3. Dynamic Power-of-10 Price Scaling (Commit `2d57657`, 2026-11-06)
- Automatically normalizes meme coin contract sizes (e.g., 1000PEPE, 1000000MOG) where chart quote conventions differ by orders of magnitude from venue base currency requirements.

### 4. Wall-Clock Bounded Latch & Auto-Healer (Commits `610e8aa` & `a32bcf7`, 2026-11-04)
- **Problem**: Transient WebSocket blips were latching the protection kill-switch permanently after 3 cycles (15s), requiring manual operator releases.
- **Solution**: Implemented a mandatory 60s wall-clock floor (`BITGET_PROTECTION_MIN_OUTAGE_SECONDS`) and the `LatchHealer` service that automatically clears latches after 6 consecutive healthy cycles for socket blips or 12 for REST provider reads.

---

## 5. Operational Commands & Verification Runbook

### On-Demand Health Snapshot
```bash
ssh valarion@46.202.155.192 "cd /home/valarion/apps/fatty-multi-exchange-trader && set -a && source .env && set +a && runtime/console-ops-venv/bin/python scripts/health_report.py"
```

### Trace Latest Execution Trail
```bash
ssh valarion@46.202.155.192 "cd /home/valarion/apps/fatty-multi-exchange-trader && runtime/console-ops-venv/bin/python scripts/dispatch_trail.py --dsn 'postgresql://fatty_app:k2kb2bdbgv@172.20.0.2:5432/fatty_trader' --latest"
```

### Check Docker Container Health
```bash
ssh valarion@46.202.155.192 "docker compose -f /home/valarion/apps/fatty-multi-exchange-trader/docker-compose.yml ps"
```

### Query Recent Dispatches from Database
```bash
ssh valarion@46.202.155.192 "docker exec -t fatty-multi-exchange-trader-postgres-1 psql -U fatty_app -d fatty_trader -c 'SELECT d.id, cs.pair_token, cs.direction, d.state, d.terminal_reason, d.created_at FROM dispatches d JOIN canonical_signals cs ON d.source_id = cs.id ORDER BY d.created_at DESC LIMIT 10;'"
```
