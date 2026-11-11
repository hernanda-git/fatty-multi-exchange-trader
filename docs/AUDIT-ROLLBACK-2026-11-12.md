# Audit fix rollback (2026-11-12)

Baseline: `pre-audit-2026-11-12` = commit `d9075a5` on `main`.
Work branch: `fix/audit-2026-11-12`. Nothing here is deployed.

Every change is a separate commit so a single fix can be reverted without
unpicking the others. All schema changes are additive and nullable: an older
image keeps running against the migrated database.

## Per-commit revert

| Commit | Scope | Revert with |
|---|---|---|
| `33a9c1d` | management source clock, failure reason, analyzer backfill | `git revert 33a9c1d` |
| `530872a` | cancel ownership, reservation leak, no-op transitions | `git revert 530872a` |
| `587c42c` | native plan reconciliation, intent churn, migration 38 | `git revert 587c42c` |
| `da59b71` | transient latch tiering, healer allowlist | `git revert da59b71` |
| `8629de6` | truthful health, reconciliation_state writes | `git revert 8629de6` |
| `0a1692d` | capability freshness, attestation mode flag | `git revert 0a1692d` |

Revert in reverse order if several are suspect. Never revert migration 38 alone:
the columns and indexes are harmless to old code, and dropping them would lose
error evidence.

## Environment rollback (no code change, takes effect on restart)

| Knob | Fix default | Old behaviour |
|---|---|---|
| `BITGET_MANAGEMENT_MAX_SOURCE_AGE_SECONDS` | `1800` | `300` restores the previous effective 5-minute budget |
| `BITGET_LATCH_TRANSIENT_STRIKES` | `3` | `1` latches on the first failed read again |
| `BITGET_LATCH_TRANSIENT_AUTORELEASE` | `1` | `0` keeps transient latches until an operator releases them |
| `BITGET_PROTECTION_VERIFICATION_STALE_SECONDS` | `3600` | a very large value disables native-verification freshness |
| `WORKER_HEALTH_ATTESTATION` | `token` | already the old behaviour; `identity` is opt-in hardening |

`BITGET_PROTECTION_CAPABILITY_GATE_ENABLED` is unchanged and still `0`. The
capability freshness fix only matters when you enable it.

## Deploy / rollback procedure (remote host)

```bash
cd /home/valarion/apps/fatty-multi-exchange-trader
git fetch && git checkout fix/audit-2026-11-12
docker compose up -d --build          # applies migration 38 on start
curl -s http://127.0.0.1:18081/health/telemetry
```

Rollback to the exact previous build:

```bash
git checkout pre-audit-2026-11-12
docker compose up -d --build
```

Rolling back the image does **not** need a database rollback. Migration 38 only
adds `live_order_intents.last_error` and two indexes.

## Rollout order that keeps blast radius small

1. Deploy `33a9c1d` alone with `BITGET_MANAGEMENT_MAX_SOURCE_AGE_SECONDS=300`.
   This changes only evidence and selection, not trading behaviour. Watch one
   management instruction end to end.
2. Raise the knob to `1800` when you accept that a 20-minute-old stop reminder
   will execute.
3. Deploy `530872a` and `587c42c` together. Watch the BRETT SL/TP intents move
   from `unknown` to `acknowledged`, and confirm `bitget_margin_reservations`
   has no `reserved` rows after the next split veto.
4. Deploy `da59b71` and watch one `provider-*-read-failed` cycle without a
   latch, then a real latch after three.
5. `8629de6` and `0a1692d` are reporting/hardening only; deploy last.

Do not deploy steps 3–5 while a position is open and unattended. Step 1–2 are
safe with a live position.

## Known residual items not changed

- `BitgetOperatorGateway.close_position` is still test-only dead code; it would
  report `closed` from a flat read. It has no production caller.
- Web-push fan-out still depends on the Telegram worker being configured.
- The three stale Oct dispatches remain non-terminal; one is excluded only by an
  audited baseline. Reconciling them is a separate data operation.
