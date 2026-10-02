# Kaka trades — paper evaluation (replay of the channel history)

Replay of the channel's own message history through the same parser and ledger rules as
the live paper lane: 1 USDT margin per leg at 20x, taker fee 0.06% per side, DCA merged
into one averaged position, market entries/exits priced from 1-minute public candles.
Nothing here is written to the paper tables; this is an offline evaluation.

- Trades closed: **27** (wins 15 / losses 12)
- Open at end of replay: 1
- Realised paper PnL: **-2.3959 USDT**
- Win rate: 55.6%
- Average per trade: -0.0887 USDT
- Channel's own reported PnL over the same trades: -52.00$ (their sizing)
- Messages skipped: 67

## Per symbol

| Symbol | Trades | Net PnL (USDT) |
|---|---|---|
| BTCUSDT | 14 | -1.5794 |
| ETHUSDT | 11 | -0.8033 |
| HYPEUSDT | 1 | -0.2155 |
| SOLUSDT | 1 | 0.2024 |

## Trades

| Closed (UTC) | Symbol | Side | Legs | Entry | Exit | Reason | PnL | Channel |
|---|---|---|---|---|---|---|---|---|
| 2026-09-12T11:49:23+00:00 | ETHUSDT | SHORT | 1 | 2378.58 | 2535.02 | manual-close | -1.3394 | 21$ |
| 2026-09-13T09:26:37+00:00 | ETHUSDT | LONG | 2 | 2507.5 | 2482 | stop-hit | -0.4548 | -46$ |
| 2026-09-13T23:41:32+00:00 | ETHUSDT | SHORT | 1 | 2507.96 | 2475.5 | manual-close | 0.2349 | 41$ |
| 2026-09-14T12:18:37+00:00 | ETHUSDT | SHORT | 1 | 2530 | 2505 | tp | 0.1736 | 32$ |
| 2026-09-14T15:48:08+00:00 | ETHUSDT | LONG | 2 | 2492.86 | 2506.76 | cancelled | 0.1750 | - |
| 2026-09-14T16:56:17+00:00 | BTCUSDT | LONG | 1 | 76150 | 78759.5 | manual-close | 0.6614 | 40$ |
| 2026-09-14T22:57:18+00:00 | BTCUSDT | SHORT | 2 | 79179.15 | 78513.8 | manual-close | 0.2881 | 23$ |
| 2026-09-15T10:05:02+00:00 | BTCUSDT | LONG | 2 | 76675 | 76905.1 | cancelled | 0.0720 | - |
| 2026-09-15T17:37:13+00:00 | BTCUSDT | LONG | 1 | 76300 | 76949.8 | manual-close | 0.1463 | 95$ |
| 2026-09-16T11:46:31+00:00 | BTCUSDT | SHORT | 1 | 75811.5 | 76200 | stop-hit | -0.1265 | -24$ |
| 2026-09-16T18:16:51+00:00 | BTCUSDT | SHORT | 1 | 76500 | 75838.4 | cancelled | 0.1490 | - |
| 2026-09-18T08:02:30+00:00 | BTCUSDT | LONG | 2 | 76575 | 74490 | stop-hit | -1.1371 | -87$ |
| 2026-09-19T13:44:22+00:00 | BTCUSDT | SHORT | 1 | 78100 | 80200 | stop-hit | -0.5618 | - |
| 2026-09-21T01:23:35+00:00 | BTCUSDT | SHORT | 2 | 80953.65 | 81980 | stop-hit | -0.5551 | -93$ |
| 2026-09-21T08:51:18+00:00 | BTCUSDT | SHORT | 1 | 82400 | 83720 | stop-hit | -0.3444 | -55$ |
| 2026-09-22T05:30:48+00:00 | BTCUSDT | SHORT | 1 | 85425.1 | 86540 | stop-hit | -0.2850 | -35$ |
| 2026-09-22T17:35:23+00:00 | ETHUSDT | LONG | 1 | 2731.06 | 2753.19 | manual-close | 0.1381 | 39$ |
| 2026-09-23T13:41:30+00:00 | BTCUSDT | SHORT | 1 | 87200 | 85800 | stop-hit | 0.2971 | 50$ |
| 2026-09-23T16:01:39+00:00 | HYPEUSDT | LONG | 1 | 93.273 | 92.38 | stop-hit | -0.2155 | -32$ |
| 2026-09-24T08:52:06+00:00 | ETHUSDT | SHORT | 1 | 2692.3 | 2663.38 | manual-close | 0.1908 | 46$ |
| 2026-09-24T11:20:46+00:00 | ETHUSDT | LONG | 1 | 2632 | 2648.34 | manual-close | 0.1002 | - |
| 2026-09-24T11:20:57+00:00 | BTCUSDT | LONG | 1 | 83350 | 83481.9 | manual-close | 0.0076 | 22$ |
| 2026-09-24T13:36:23+00:00 | BTCUSDT | SHORT | 1 | 83403.3 | 84100 | stop-hit | -0.1911 | -30$ |
| 2026-09-25T08:29:35+00:00 | ETHUSDT | SHORT | 1 | 2710 | 2750 | stop-hit | -0.3192 | -28$ |
| 2026-09-25T14:06:29+00:00 | ETHUSDT | SHORT | 1 | 2739 | 2680.22 | manual-close | 0.4052 | - |
| 2026-09-27T07:33:56+00:00 | SOLUSDT | LONG | 1 | 120.607 | 121.972 | manual-close | 0.2024 | - |
| 2026-09-28T03:01:01+00:00 | ETHUSDT | SHORT | 1 | 2689.74 | 2701 | stop-hit | -0.1077 | -31$ |
| - | SOLUSDT | LONG | 1 | 118.343 | - | still-open-at-end-of-replay | open | - |

## Skipped messages

- `143`: media-only
- `146`: media-only
- `147`: already-open
- `148`: media-only
- `151`: media-only
- `159`: media-only
- `162`: media-only
- `168`: media-only
- `174`: media-only
- `177`: media-only
- `179`: media-only
- `185`: media-only
- `186`: unmatched:TP
- `187`: media-only
- `188`: unmatched:STOP_MOVE
- `189`: unmatched:TP
- `194`: media-only
- `199`: media-only
- `201`: media-only
- `204`: unmatched:STOP_MOVE
- `205`: media-only
- `206`: unmatched:BREAKEVEN
- `214`: unmatched:CANCEL
- `216`: already-open
- `217`: media-only
- `224`: unmatched:ADD
- `225`: media-only
- `226`: unmatched:CLOSE
- `227`: media-only
- `229`: media-only
- `233`: unmatched:TP
- `234`: media-only
- `235`: unmatched:STOP_MOVE
- `236`: unmatched:STOP_HIT
- `241`: media-only
- `245`: media-only
- `249`: media-only
- `253`: media-only
- `255`: media-only
- `258`: unmatched:STOP_HIT
- `260`: media-only
- `262`: media-only
- `265`: unmatched:STOP_MOVE
- `266`: media-only
- `267`: unmatched:TP
- `268`: unmatched:TP_REMOVED
- `269`: unmatched:STOP_HIT
- `271`: media-only
- `273`: media-only
- `276`: media-only
- `280`: media-only
- `282`: media-only
- `285`: media-only
- `286`: media-only
- `288`: media-only
- `294`: media-only
- `295`: already-open
- `296`: media-only
- `300`: media-only
- `304`: media-only
