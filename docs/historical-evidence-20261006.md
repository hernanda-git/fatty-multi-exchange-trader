# Authenticated historical evidence: 17 unresolved Bitget ENTRY owners

## Outcome and boundary

Fresh collection covered all **17** unresolved ENTRY rows, **12** symbols and
**526** authenticated provider GET responses. Full exact entry → close → provider
position-epoch ownership remains **UNPROVEN for all 17**. This is a diagnostic
collector/negative-only assessor, not a general positive recovery verifier or
an authorization to apply recovery. It has no provider mutation interface and no
DB write/apply implementation. Production DB, provider state, config and latches
were not changed.

Canonical private artifacts (all files owner-readable 0600, directory 0700):

`/home/valarion/workspace/dev/bitgetevidence20261006/artifacts/final-20261006/`

- `database.json`: explicit `BEGIN READ ONLY` snapshot; 17 target ENTRY owners,
  all Bitget intents/fills, reservations, immutable legacy bindings, close
  bindings and fallbacks. Decimal numeric values are preserved as strings.
- `<SYMBOL>.json`: complete collected raw pages, cursors, per-window status,
  authenticated account provenance, entry order details, archived plans, targeted
  exact SL/TP clientOid lookups, executed-plan order detail and fill-history reads,
  and final position observation.
- `<SYMBOL>-responses.json`: every raw provider envelope and HTTP status emitted
  by that symbol probe, including terminal null-list and explicit-empty pages.
- `per-entry-verdicts.json`: all 17 unique owner verdicts, exact provider entry
  receipts, durable-fill matching/economics, named plan/emergency links, and
  explicitly **unbound** symbol close/position-history candidates.
- `endpoint-contracts.json`, `manifest.json`: source contract caveats and hashes.

Earlier `fresh-20261006/` and `fresh-complete-20261006/` artifacts are exploratory
and superseded, especially their initial null-list classification and signed-fee
comparison. Use only `final-20261006/` for handoff.

## Proven narrower observations

- **17/17** exact provider ENTRY order/clientOid plus provider fill/quantity
  receipts are present. This is not durable ledger completion.
- **11/17** have exact durable entry fill IDs. **6/17** have no durable entry fills:
  NOT, initial WLD 12, GRASS 15, INJ 0.9, ETHFI 6.8, EUL 26.9. The assessment's
  `exact-entry-provider-identity-unproven` blocker refers to this **durable**
  matching requirement, not to absence of authenticated provider ENTRY receipts.
- All **11** durable entry fill economics match `/order/fills` after the existing
  documented application normalization of negative provider fee to positive cost.
  Only **3** match higher-precision `/order/fill-history` economics exactly:
  WLD 91, WLD short 80, WLD 72. The other **8** have source precision differences;
  neither old fills nor provider responses were quantized or rewritten.
- Exact `ENTRY-clientOid + '-emergency'` close naming occurs for NOT, WLD 12,
  GRASS and INJ. That is a naming link, not provider position-epoch ownership.
- PUMP 1880 has the strongest archived native chain:
  `live-bitget-PUMPUSDT-ce1fef05199455a8-sl` → plan
  `1489461465550675968` → executed order `1489566819033956353` → fill
  `1489566819242741760`. Its TP plan `1489461465496150017` was cancelled.
  Order history also reports the SL plan ID as close clientOid. Exact lookups by
  both SL and TP clientOid returned the same corresponding plans.
- That native chain still lacks an immutable entry-epoch → plan binding and a
  shared authenticated position identity linking entry and close. Position
  history candidate `1489450878442815494`, epoch `1790838682756`, is retained
  **unbound**. Quantity/price/timestamp similarity does not authorize binding.

## Per-entry results

All rows below have authenticated ENTRY receipts and an UNPROVEN full-epoch verdict.
The full exact identifiers are retained in the private JSON, not shortened there.

| Symbol / quantity | ENTRY clientOid suffix | Durable entry fill ID present | Exact named close/plan link |
|---|---|---|---|
| NOT 10820 | 7136e226e8124862-1788839186 | no | emergency close |
| WLD 12 | 6072e55edd0c4b86 | no | emergency close |
| GRASS 15 | c000000000000000 | no | emergency close |
| INJ 0.9 | 02de5cb48e114916 | no | emergency close |
| ETHFI 6.8 | 23c8dc144553454f | no | absent |
| EUL 26.9 | 9ea05801e066409a | no | absent |
| WLD 91 | d47acac9271651b2 | yes | absent |
| WLD short 80 | d1111f6ed5f4508a | yes | absent |
| SNDK 0.018 | ef3d1b13d05c5824 | yes | absent |
| WLD 72 | 3490b8b58cdc53b3 | yes | absent |
| 1000BONK 8843 | 23b224a43bd250c4 | yes | absent |
| PENGU 4102 | ca2b790b8a655541 | yes | absent |
| STONK 36 | cd6bedb6c3e356f6 | yes | absent |
| HBAR 156 | 08fb1098fc7953e5 | yes | absent |
| PUMP 4001 | 7953fefc297b5e22 | yes | absent |
| PENGU short 5082 | e8dcda3560325754 | yes | absent |
| PUMP 1880 | ce1fef05199455a8 | yes | SL plan → executeOrderId → fill |

## Source contracts and completeness limitations

Fresh official references:

- https://www.bitget.com/docs/catalog/classic-contract-trade/classic-contract-trade
- https://www.bitget.com/docs/catalog/classic-contract-plan/classic-contract-plan
- https://www.bitget.com/docs/catalog/classic-contract-position/classic-contract-position

Old `/api-doc/contract/...` URLs redirect to unrelated unified-account material;
that is not a source contract. The new classic documentation specifies
`fill-history` maximum one-week intervals, limit 100 and `idLessThan = endId`;
orders history supports 90-day retention; plan history supports a three-month
interval and omitted `planStatus` queries all statuses. Both generic all-plan-type
history and exact SL/TP lookups were collected. No executed-only filtering was
used, so cancelled TP evidence is preserved too.

**The fresh docs describe history lists as arrays and endId as string, but do not
explicitly document terminal all-null list/cursor semantics.** The real responses
consistently returned success `00000` with `fillList:null/endId:null` or
`entrustedList:null/endId:null` after nonempty short pages and for empty windows.
These are preserved as `successful-null-list-unproven`, not malformed API errors,
not silently normalized to `[]`, and not treated as proof of full exhaustion.
`/order/fills` and position history instead returned explicit empty arrays after
pagination. Their observed termination must not be transplanted to the historical
fill/order/plan contracts. Provider confirmation or an authoritative documented
null-terminal contract remains needed before a source-complete historical claim.

The weekly PUMP native-plan query also returned **two plan rows outside its
requested first window** (Oct 1 plans in a Sept 22–29 query). Both are flagged in
`out_of_window_rows`; raw pages remain intact. A separate full native-plan scope
query and exact-clientOid queries reproduced those plans. Do not call the weekly
native plan results nonoverlapping/exhaustive solely because time params were sent.

Other universal blockers: provider entry/close orders and fills do not expose the
position-history positionId/epoch as a shared immutable ownership key; legacy
fallback epochs are absent; durable historical close ownership cannot be inferred
from operator IDs, same-symbol opposing trades or current flat. Missing durable
entry/close fills are an additional implementation seam, not permission to create
synthetic fills. Historical economic precision must remain source-specific.

## Reproduction and tests

Run from the private checkout, without sourcing production `.env`:

```sh
python -m pytest -q tests/test_historical_evidence_collector.py
ruff check scripts/collect_bitget_historical_evidence.py tests/test_historical_evidence_collector.py
ruff format --check scripts/collect_bitget_historical_evidence.py tests/test_historical_evidence_collector.py
python scripts/collect_bitget_historical_evidence.py \
  --project /home/valarion/apps/fatty-multi-exchange-trader \
  --output /home/valarion/workspace/dev/bitgetevidence20261006/artifacts/<NEW-PRIVATE-DIRECTORY>
```

Artifacts are created exclusively, so existing receipts are never overwritten.
`--analyze-only --output <directory>` performs no external access but requires a
new/nonexistent verdict file. No `--apply` option exists. Host credentials are
never read; only healthy `monitor-bitget` executes the streamed collector source
using `/app/.venv/bin/python -`. `GetOnlyTransport` rejects every non-GET request
at the final HTTP boundary, even if a future caller invokes a mutating client API.

Targeted RED/GREEN coverage: 18 passed; bounded gap-free weekly windows, cursor
following after short pages, raw null/empty retention, malformed/cursor failure,
GET transport fence, all source families/native-plan types, exact-clientOid
lookups and execute-order chains, Decimal precision and exclusive 0600 files,
READ ONLY/monitor-only orchestration, source-precision non-rewriting, epoch proof
refusal, offline-only analysis and out-of-window diagnostics. No real-PostgreSQL
mutation/concurrency test or production deployment is claimed or needed for this
GET-only artifact. Full project test suite was not run in this collector checkout.
