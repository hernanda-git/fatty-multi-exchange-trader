# Incident Report: PEPE Short Limit Parsing & Fallback Gap

**Date**: 2026-11-08 / 2026-11-09  
**Affected Symbol**: PEPEUSDT  
**Message IDs**: #16468 (Signal), #16472 (Take-Profit Announcement)  
**Severity**: Medium (Missed Entry Opportunity; Zero Capital Loss)  
**Status**: Resolved & Deployed (Commit `916fd97`)

---

## 1. Summary

On 2026-11-08 at 18:24:39 UTC, Telegram message #16468 was ingested:
```text
$PEPE short limit around 0.51 - 0.513

Stoploss placed right above todays high at 0.521
```
The message had an attached TradingView screenshot showing a short setup. 

At 21:59:18 UTC, message #16472 arrived:
```text
$PEPE full tp booked at 3R +

This trade was pre analysed on our market update video❤️
```
The trade had reached 3R, but the bot had not entered because message #16468 produced no canonical signal in PostgreSQL.

---

## 2. Root Cause Analysis

Two interacting defects prevented admission:

1. **OCR / Vision Range and Scientific Notation Parsing Gap**:
   - The image analyzer (`image_analysis.py`) prompted Codex / Vision model to extract structured JSON.
   - The model returned:
     - `entry`: `"0.51–0.513"` (string range with en-dash)
     - `stop_loss`: `"0.521"`
     - `levels.chart_entry_zone`: `"0.50546e-5–0.51928e-5"`
     - `targets`: `["0.46933e-5"]`
   - Direct `Decimal(str(...))` calls threw `InvalidOperation` when encountering range strings, causing `signal_from_image_json` to return `None`.

2. **Absence of Text Fallback for Media Messages**:
   - In `postgres_worker.py`, when a message had `has_media = True`, it attempted vision analysis.
   - If image analysis returned no signal (`image_signal is None`), the worker immediately treated the analysis as complete with `CanonicalSignal = None`.
   - It did not fall back to `parse_explicit_signal()` against `raw_text`, even though the raw text explicitly declared the entry and stop loss.

3. **Meme Coin Unit Mismatch**:
   - PEPE trades on Bitget as `1000PEPEUSDT` or `PEPEUSDT` where prices are around `0.0000051`.
   - The signal stated `0.51` (unscaled chart units, a 100,000x multiplier).
   - If target levels were extracted from chart scientific notation (`0.46933e-5`) while entry was unscaled (`0.51`), geometry validation rejected the setup because targets and entries were in different orders of magnitude.

---

## 3. Remediation & Implementation

Fixed in commit `916fd97`:

1. **Robust Numerical Extraction (`image_analysis.py`)**:
   - Added `_extract_first_decimal()` and `_extract_all_decimals()` using regex `(\d+(?:\.\d+(?:[eE][+-]?\d+)?)?)`.
   - Handles ranges, prefixes, and en-dashes gracefully.
   - Implemented power-of-10 ratio harmonization to align chart-unit targets with entry magnitude.

2. **Regex Expansion (`deterministic_parser.py`)**:
   - Broadened `_LIMIT_ENTRY` to capture ranges (`around 0.51 - 0.513`) and varied casing/tense (`placed short limit`, `placing short limit`, `shorted`).
   - Broadened stop-loss matching to handle typos (`STOPPOSS`, `STOP LOSS`).

3. **Deterministic Text Fallback (`postgres_worker.py`)**:
   - If image processing fails or yields no valid signal, and the message does not explicitly stand down, `postgres_worker.py` invokes `parse_explicit_signal(message.raw_text)`.
   - If explicit signal is found, it is accepted under status `FALLBACK_ACCEPTED`.

4. **Dynamic Price Scaling (`price_scale.py`)**:
   - Integrated power-of-10 scaling to safely map meme tokens (100,000x) between chart conventions and Bitget exchange contract precision.

---

## 4. Verification

1. **Unit Regression Suite**: `tests/unit/test_pepe_signal_audit_fix.py` created with 5 exhaustive tests covering message #16468, chart JSON extraction, scaling, and historical edge cases. All passed.
2. **Production Deployment**: Containers `analyzer`, `source-management`, and `dispatcher-bitget` rebuilt and restarted on 2026-11-09 01:49 UTC. All 10/10 containers healthy.
