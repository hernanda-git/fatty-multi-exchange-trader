# Kaka trades — Additional PAPER Signal Source: Implementation Plan

> **For Hermes:** Use subagent-driven-development to implement this plan task-by-task.
> **Mode: PAPER ONLY.** No provider mutation, no live order, no change to the live Bitget lane.

**Goal:** Ingest the Telegram channel `Kaka trades` (id `-1003763643270`, `@Kakatrades`) as a
second signal source, parse its message formats into canonical signals, and "execute" them in
an isolated paper ledger priced from the public ticker — so we can measure whether mirroring
that channel is profitable before any real money is involved.

**Architecture:** A self-contained paper lane. `intake` gets one more source channel; a new
`paper-kaka` worker consumes those messages, classifies them (deterministic parser first, Codex
as fallback), opens/closes rows in new paper-only tables at public market prices, and posts a
daily digest to the operator chat. It never touches `dispatches`, `live_order_intents`,
`fills`, `fallback_protection`, the kill switch, or the Bitget adapter.

**Tech Stack:** Python 3.12, psycopg, Telethon (existing intake session), Codex CLI (existing
analyzer auth), PostgreSQL 16 in Compose, pytest.

---

## 1. What the channel actually publishes (learned from 171 messages, 2026-09-01 → 2026-09-28)

Composition of the dump: **124 text messages, 47 media-only** (charts). Only 2 messages carry
both text and media, so the text stream is self-sufficient for v1.

### 1.1 Message families and their real shapes

| Family | Count (approx) | Examples (verbatim) |
|---|---|---|
| Structured SETUP block | ~18 | `🧨BTC LONG  SETUP` / `🎯 Just follow the plan.` / `📍 Entry: $76,150` / `💰 Margin: $280` / `➕ DCA: $76,450` / `🛑 SL: $75,210` |
| Short entry line | ~25 | `Short $ETH \| SL -2401` · `Long -$ETH \| SL -2479` · `Short -$ETH AGAIN \| ENTRY -2739 \| 2ND DCA -2775 \| SL -2790` · `Long -$SOL \| 2ND DCA -118.50 \| SL 117.50` |
| Take-profit updates | ~10 | `Tp -2540` · `Tp hit \| Profit+32$` · `Remove tp` |
| Stop management | ~15 | `Set sl at be` · `Set your SL at 92.38.` · `Move sl -74,980` · `SL 76,200` · `Set your SL at 86,200 so that even if the market moves up, we can still stay in profit.` |
| Add/DCA instructions | ~8 | `Take 2nd entry 2475` · `Take entry now` · `We'll use the remaining balance for our 2nd entry at $81,500.` |
| Closures | ~14 | `Closed \| Profit -21$` · `Close \| Profit +95$` · `Trade Closed 🔴 \| Loss: -$10` · `Closed` + separate `Profit +29$` |
| Stop hits | ~20 | `SL hit \| Loss -46$` · `SL hit again \| Loss -44$` · `❌ SL HIT \| Loss: -$30` · `SL hit \| Profit+50$` (stop moved into profit) · `Our SL got hit, but we ended the trade at breakeven` |
| Cancellations | ~6 | `Cancelling the entry for now…` · `We're cancelling the second entry…` · `Forget it,. Let's cancel the trade for today.` · `Don't take this LONG entry yet.` |
| Noise (never a signal) | ~25 | referral links (`partner.blofin.com`, Bitunix), `Wallet -444$`, challenge promos, `See you tomorrow`, `👍👍👍`, running commentary |

### 1.2 Parsing traps (each one already seen in the data)

1. **Leading dash is a separator, not a minus sign.** `SL -2401` means stop at 2401,
   `Tp -2540` means target 2540, `Move sl -74,980` means 74980. Naive numeric parsing yields
   negative prices and the geometry validator would reject every one of these.
2. **Thousands separators.** `$76,150`, `74,980`, `81,500` must be stripped before `Decimal`.
3. **Direction vocabulary is inconsistent:** `Short $ETH`, `Long -$ETH`, `Eth long`,
   `BTC LONG  SETUP`, `-$btc`, `Short again`, `Long eth`. Case and order both vary.
4. **Entry is optional** in the short form (`Short $ETH | SL -2401`), which is the same
   market-entry situation already solved for fattyfatclub: entry = market at decision time,
   fail-closed when the stop is already crossed.
5. **`DCA` / `2ND DCA` / "second entry"** is a scale-in level, not a target. It must not be
   confused with an entry price or a TP.
6. **`Margin: $280` is the channel's own sizing**, irrelevant to us (our cap is 1 USDT per
   trade).
7. **Closures and results arrive as separate messages** (`Closed` then `Profit +29$`), and
   `Remove tp` / `Set sl at be` are management, not trade opens.
8. **`SL hit | Profit+50$`** means the stop had been moved above entry — a win, not a loss.
   Any naive "SL hit = loss" rule mis-scores this.

### 1.3 Channel character (relevant to the paper-first decision)

The channel's own reported wallet walks from **+555$ down to -320$** across September, with many
`SL hit | Loss` messages and self-aware notes ("I'm sorry for the losses, everyone… I'll focus on
playing it much safer"). Trades are BTC/ETH/SOL/HYPE, held minutes to hours, frequently scaled in
and stopped out. This is exactly the case where mirroring must be *measured* before it is trusted.

---

## 2. Assumptions and constraints

- Paper lane is additive: existing `bitget` dispatches, canary cap, kill switch and reservation
  logic stay untouched. The only shared component is the `intake` Telethon session.
- `DISPATCH_EXCHANGES` and `enabled_dispatch_exchanges()` remain `{binance, bitget}`. The paper
  lane does **not** add a pseudo-exchange to `dispatches`; it keeps its own tables so a paper
  bug can never reach the live executor.
- No image analysis in v1 (Kaka's 47 media-only posts are ignored). `intake` currently stores
  `has_media` but no `media_path`; enabling image analysis is a separate, later decision.
- Our sizing rule is unchanged: margin cap 1 USDT per trade, leverage 20x ceiling, floor 5x,
  stop must clear the liquidation guard — the same `live_policy` math, reused for the paper
  ledger so paper results are comparable to what the real lane would have done.

---

## 3. Proposed approach

```
intake (existing)
  └─ source channels: @fattyfatclub, @Kakatrades      ← +1 config value
        │  (telegram_messages rows, unchanged)
        ▼
paper-kaka worker (new compose service)
  ├─ claims RECEIVED messages of the Kaka channel
  ├─ kaka_parser.parse(...)  → KakaEvent (OPEN | TP | SL | ADD | CLOSE | CANCEL | NOISE)
  │    └─ fallback: Codex classifier for anything the parser does not recognise
  ├─ paper ledger (2 new tables): paper_kaka_trades, paper_kaka_events
  │    ├─ OPEN  → size via live_policy math, price = public ticker (no creds)
  │    ├─ SL/BE/TP/ADD → mutate the open paper trade's levels
  │    └─ CLOSE/SL-hit → realise PnL at the stated/observed price
  └─ daily digest → notifications_outbox (operator chat)
```

Why a dedicated worker instead of extending `analyzer`: the analyzer's fan-out writes `dispatches`
that the live dispatcher will pick up. Keeping Kaka out of that path removes the only realistic
way a paper source could ever reach the money lane.

### Data model (new migrations 18, 19)

```sql
CREATE TABLE paper_kaka_trades (
    id UUID PRIMARY KEY,
    source_message_id BIGINT NOT NULL,          -- telegram_messages.message_id
    symbol TEXT NOT NULL,                       -- e.g. ETHUSDT
    side TEXT NOT NULL CHECK (side IN ('LONG','SHORT')),
    entry_price NUMERIC NOT NULL,
    stop_loss NUMERIC NOT NULL,
    take_profit NUMERIC,
    size_notional NUMERIC NOT NULL,             -- from live_policy sizing
    margin_usdt NUMERIC NOT NULL,
    leverage INT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('open','closed','cancelled')),
    close_price NUMERIC,
    close_reason TEXT,                          -- 'tp' | 'sl' | 'be' | 'manual-close' | 'cancel'
    realized_pnl_usdt NUMERIC,
    opened_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    UNIQUE (source_message_id)
);

CREATE TABLE paper_kaka_events (
    id UUID PRIMARY KEY,
    trade_id UUID REFERENCES paper_kaka_trades(id),
    source_message_id BIGINT NOT NULL,
    event_type TEXT NOT NULL,                   -- OPEN|TP|SL_MOVE|BE|ADD|CLOSE|CANCEL|NOISE
    parsed_json JSONB NOT NULL,                 -- what the parser extracted, for audit
    parse_path TEXT NOT NULL,                   -- 'deterministic' | 'codex'
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_message_id, event_type)
);
```

---

## 4. Step-by-step plan

### Task 1: Record the learned formats as failing parser tests

**Objective:** Pin the eight real message shapes from §1.1 before writing any parser.

**Files:** Create `tests/unit/test_kaka_parser.py`

**Step 1: Write failing tests**

```python
from decimal import Decimal
from fatty_trader.kaka.parser import KakaEventType, parse_kaka_event


def test_structured_setup_block_parses_entry_stop_and_direction():
    event = parse_kaka_event(
        "🧨BTC LONG  SETUP\n🎯 Just follow the plan.\n📍 Entry: $76,150\n"
        "💰 Margin: $280\n➕ DCA: $76,450\n🛑 SL: $75,210",
        message_id=157,
    )
    assert event.type is KakaEventType.OPEN
    assert event.symbol == "BTCUSDT"
    assert event.side == "LONG"
    assert event.entry == Decimal("76150")
    assert event.stop_loss == Decimal("75210")
    assert event.dca == Decimal("76450")


def test_leading_dash_is_a_separator_not_a_minus():
    event = parse_kaka_event("Short $ETH | SL -2401", message_id=145)
    assert event.side == "SHORT"
    assert event.symbol == "ETHUSDT"
    assert event.stop_loss == Decimal("2401")          # not -2401


def test_next_entry_with_2nd_dca_and_sl():
    event = parse_kaka_event(
        "Short -$ETH AGAIN | ENTRY -2739 | 2ND DCA -2775 | SL -2790", message_id=298
    )
    assert (event.entry, event.dca, event.stop_loss) == (
        Decimal("2739"), Decimal("2775"), Decimal("2790")
    )


def test_stop_manager_messages_are_not_opens():
    assert parse_kaka_event("Set sl at be", message_id=301).type is KakaEventType.BREAKEVEN
    assert parse_kaka_event("Move sl -74,980", message_id=192).stop_loss == Decimal("74980")
    assert parse_kaka_event("Remove tp", message_id=268).type is KakaEventType.TP_REMOVED


def test_close_and_result_messages():
    assert parse_kaka_event("Closed", message_id=302).type is KakaEventType.CLOSE
    assert parse_kaka_event("Close | Profit +95$", message_id=197).reported_pnl == Decimal("95")
    assert parse_kaka_event("SL hit | Loss -46$", message_id=156).type is KakaEventType.STOP_HIT


def test_stop_hit_in_profit_is_a_win_not_a_loss():
    event = parse_kaka_event("SL hit | Profit+50$", message_id=256)
    assert event.type is KakaEventType.STOP_HIT
    assert event.reported_pnl == Decimal("50")


def test_cancels_and_noise_never_open_a_trade():
    for text in (
        "Cancelling the entry for now. The market is highly volatile",
        "We're cancelling the second entry. Our SL will remain at 2485.",
        "Don't take this LONG entry yet.",
        "Wallet -444$",
        "See you tomorrow.",
        "👍👍👍",
        "https://partner.blofin.com/d/KakaTrades",
    ):
        assert parse_kaka_event(text, message_id=1).type in {
            KakaEventType.CANCEL, KakaEventType.NOISE
        }
```

**Step 2:** `pytest tests/unit/test_kaka_parser.py -q` → expect `ModuleNotFoundError`/failures.

### Task 2: Implement the deterministic parser

**Files:** Create `src/fatty_trader/kaka/__init__.py`, `src/fatty_trader/kaka/parser.py`

Rules: strip `$`, `,`, and a leading dash before number parsing; require a symbol and a
side for OPEN; require an SL clause for OPEN; treat `Set sl at be` / `at breakeven` as
BREAKEVEN; `Remove tp` as TP_REMOVED; unknown text → NOISE (never OPEN).

**Verify:** `pytest tests/unit/test_kaka_parser.py -q` → all pass; then run the parser over the
real dump and print the classification histogram:

```bash
python - <<'PY'
import json
from collections import Counter
from fatty_trader.kaka.parser import parse_kaka_event
rows = [json.loads(l) for l in open("/home/valarion/dumps/kaka_trades_since_20260901.jsonl")]
c = Counter(parse_kaka_event(r["text"], message_id=r["message_id"]).type for r in rows if r["text"].strip())
print(c)
PY
```

Expected shape: ~18 OPEN, ~20 STOP_HIT, ~14 CLOSE, ~15 SL moves, ~25 NOISE, 0 unclassified.
**Every unclassified text is a bug to fix or a rule to add — the histogram is the acceptance gate.**

### Task 3: Public-price paper fills

**Files:** Create `src/fatty_trader/kaka/paper_fills.py`; reuse `analyzer/market_price.py`

**Tests:** price comes only from the public ticker; a lookup failure must refuse to open a
trade (no invented entry); geometry (long stop below entry, short stop above) is enforced by
reusing `CanonicalSignal` validation.

### Task 4: Paper ledger tables + repository

**Files:** Modify `src/fatty_trader/storage/migrations.py` (add 18, 19 as in §3);
Create `src/fatty_trader/kaka/paper_store.py`

**Tests:** `tests/unit/test_kaka_paper_store.py` with a fake cursor: open → row; `Set sl at be`
moves the stop to entry; STOP_HIT realises PnL at the stop (or at the moved stop);
duplicate `source_message_id` is ignored (idempotent).

### Task 5: The paper worker loop

**Files:** Create `src/fatty_trader/kaka/worker.py`; wire a `paper-kaka` branch into
`src/fatty_trader/service.py`; add the service to `docker-compose.yml`

Behaviour: claim `telegram_messages` rows for `channel_id = -1003763643270` that are `RECEIVED`,
parse, apply to the ledger, mark the row consumed. Reuse the analyzer's crash-safety style
(bounded batch, one transaction, failures logged with the message id).

**Tests:** `tests/unit/test_kaka_worker.py` — a batch of the eight real shapes produces exactly
one open trade, the right level updates, and one realised result; a poison message marks itself
failed without killing the batch.

### Task 6: Guardrails that prove paper cannot reach the venue

**Files:** `tests/unit/test_kaka_paper_isolation.py`

```python
def test_paper_worker_never_touches_live_paths():
    source = (REPO_ROOT / "src/fatty_trader/kaka/worker.py").read_text()
    for forbidden in ("BitgetLiveClient", "live_order_intents", "fallback_protection",
                      "dispatches", "BITGET_API_KEY", "BITGET_EXECUTION_ENABLED"):
        assert forbidden not in source


def test_paper_service_has_no_provider_credentials():
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    block = compose.split("paper-kaka:", 1)[1].split("\n  [a-z]", 1)[0]
    assert "BITGET_API_KEY" not in block
    assert "BITGET_API_SECRET" not in block
```

### Task 7: Enable the channel for intake (still no trading)

**Files:** Modify `docker-compose.yml` (`TELEGRAM_SOURCE_CHANNELS: @fattyfatclub,@Kakatrades`)

**Verify:** after recreate, `telegram_messages` gains rows with `channel_id = -1003763643270`
within one catch-up cycle (≤60s), and the **live** lane sees nothing (0 new `dispatches`,
0 new `live_order_intents`).

### Task 8: Daily paper digest

**Files:** `src/fatty_trader/kaka/digest.py` + reuse `notifications_outbox`

Content: trades opened/closed in the last 24h, win/loss, realised paper PnL vs the channel's own
reported numbers, and current open paper trades. One message per day to the operator chat.

### Task 9: Evaluation report after ≥2 weeks of paper data

**Files:** `docs/KAKA-PAPER-EVALUATION.md`

Metrics: signal count, parse coverage, mirrored win rate, expectancy per trade in USDT, max
drawdown, and the gap between our paper fills and the channel's reported results. Only after this
is filled in does "promote to a real source" become a decision with evidence.

---

## 5. Files likely to change

- Create: `src/fatty_trader/kaka/{__init__,parser,paper_fills,paper_store,worker,digest}.py`
- Create: `tests/unit/test_kaka_{parser,paper_store,worker,paper_isolation}.py`
- Modify: `src/fatty_trader/storage/migrations.py` (migrations 18–19)
- Modify: `src/fatty_trader/service.py` (a `paper-kaka` branch; no change to live branches)
- Modify: `docker-compose.yml` (new `paper-kaka` service; extra source channel)
- Create: `docs/KAKA-PAPER-EVALUATION.md`

## 6. Validation

1. `pytest -q` green (currently 604 passed / 4 skipped) plus the new suites.
2. Parser histogram over the real dump: 0 unclassified text messages.
3. Isolation tests pass (paper code contains no venue adapter, no live tables, no credentials).
4. After enabling the channel: intake ingests it, and `dispatches` / `live_order_intents` /
   `fills` counts do not move at all.
5. Provider account read: 0 new orders, 0 new positions while the paper lane runs.

## 7. Risks, tradeoffs, open questions

- **Media-only signals (47 messages).** If some entries exist only as images, v1 will miss them.
  Open question: do we download media and enable image analysis for Kaka only?
- **DCA is not modelled in v1.** The channel routinely takes a second entry; ignoring it makes
  paper results *less* favourable than the source's own. Options: ignore (v1), or add a second
  paper leg. Needs a decision before Task 5 is written, because it changes the ledger shape.
- **Management semantics.** `Set sl at be` after a partial TP differs from a bare BE move; the
  channel does both. v1 treats any BE instruction as "move stop to entry".
- **Their sizing vs ours.** Paper uses our 1 USDT cap, so absolute PnL will look smaller than
  the channel's $ figures; only percentages and expectancy are comparable.
- **Channel is not a licensed/consistent signal service** (referral links, losses, occasional
  cancellations mid-trade). Paper mode is the right containment.
- **Session coupling.** Both channels share one Telethon session; keep the single-consumer rule
  from the 2026-09-27 incident (no host listener on the same session).

## 8. Open questions for the operator

1. Model DCA/second entries in the paper ledger, or ignore them in v1?
2. Should paper trades mirror the channel's `Margin:` field for comparability, or keep our
   1 USDT cap (recommended: keep ours — it is what the live lane would do)?
3. Daily digest to the current operator chat, or a dedicated channel?
4. Evaluation window: 2 weeks or 1 month before deciding on promotion?
