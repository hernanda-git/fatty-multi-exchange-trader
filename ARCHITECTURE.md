# Architecture

`Telegram source -> durable intake -> canonical analysis -> atomic fan-out -> isolated exchange dispatchers`

This document describes the remediation candidate, not blanket LIVE
readiness. See [release gates](docs/remediation-verification.md) and
[lessons](docs/LESSONS_LEARNED.md). Actual deployed mode, gates, schema and image
must be read back separately. During remediation, new Bitget entries and
fallback/stream mutations remain closed. A provider account being LIVE does not
mean entry execution is enabled.

## Source-to-execution boundaries

- Intake retains source history and independent catchup coverage. Source expiry,
  original captions and source revisions govern execution eligibility.
- Analysis uses bounded, explicitly authenticated subprocesses with no ambient
  DB/exchange/Telegram credentials. Database savepoints isolate bad messages.
- Canonical signal persistence and isolated per-exchange fan-out keep one
  interpretation separate from execution outcomes.
- Preflight validates symbol metadata, risk geometry, liquidation protection,
  provider exposure and fresh balance. Atomic PostgreSQL reservations fence
  margin, symbol ownership and position slots.
- A durable intent/client OID precedes POST. Ambiguous transport or missing detail
  invokes provider GET-only reconciliation, never a freshly renamed entry retry.
- Provider fills determine execution truth. Verified stop protection is a
  separate state; FILLED is not equivalent to PROTECTED.

## Native and fallback protection

Native position TPSL is the primary design. Documented omitted execute-price
fields retain market semantics after strict market-only validation. Generic
`43011` parameter validation must not be classified as unsupported capability.
Verify pending plans by exact symbol, side, trigger, quantity, identity and market
order type; placement acknowledgement alone is insufficient.

A fallback registry row does not prove active protection. Enforcement additionally
requires explicit mutation authorization, fresh observations, durable owned
position epoch/environment and a fenced reduce-only close intent. Old rows may
not act on replacement/manual positions. Unknown/malformed provider reads cannot
terminalize a close or release admission ownership.

Close submissions are not immediately declared filled. Actual matching provider
fills drive economic accounting, confirmed owned flatness drives final recovery,
and verified-close evidence controls release of consumed ownership.

## Risk and lanes

Sizing uses configured policy and strict exchange precision/minimum validation;
this document does not override the configured allocation, leverage, caps or
canary gate. Margin and position ownership commitments include in-flight work.
PAPER accounting/replay is a separate non-LIVE lane and must not claim measured
LIVE performance or rewrite historical realized PnL during deployment.

## End-to-end diagrams

### Components

```mermaid
flowchart TB
  T[Telegram source] -->|retain and revise| I[Durable intake]
  I -->|eligible source| A[Canonical analysis]
  A -->|atomic fanout| D[Dispatch queue]
  D -->|claim and preflight| R[Risk admission]
  R -->|durable intent| X[Exchange adapter]
  X -->|order and fill truth| V[Provider account]
  V -->|verify plans| P[Protection monitor]
  P -->|audit and recovery| DB[PostgreSQL ledger]
  D -->|status| O[Notification outbox]
  DB -->|read only| H[Operator health]
```

### Execution sequence

```mermaid
sequenceDiagram
  participant S as Source
  participant I as Intake
  participant A as Analyzer
  participant Q as Dispatch
  participant DB as PostgreSQL
  participant V as Venue
  S->>I: New source message or revision
  I->>DB: Retain source evidence and eligibility
  I->>A: Eligible input with original caption
  A->>DB: Canonical result in message savepoint
  A->>Q: Eligible per-exchange dispatch
  Q->>DB: Atomic ownership and margin admission
  Q->>DB: Stable durable order identity
  Q->>V: Submit once
  alt Ambiguous response
    Q->>V: GET order/fills by durable identity
  else Known provider fill
    Q->>DB: Record actual fill truth
  end
  Q->>V: Place and GET-verify native loss stop
  Q->>DB: Persist separate protection verdict
```

### Failure decisions

```mermaid
flowchart LR
  S[Source input] -->|validate eligibility| E{Fresh and current?}
  E -->|no| A[Audit only]
  E -->|yes| Q[Claim dispatch]
  Q -->|risk rejected| R[Persist refusal]
  Q -->|provider ambiguous| G[GET only reconciliation]
  G -->|unknown| U[Escalate uncertainty]
  G -->|matching fills| F[Persist fill truth]
  F -->|verify loss stop| P{Protection proven?}
  P -->|yes| V[Protected outcome]
  P -->|no| D[Degraded exposure]
  D -->|approved owned enforcement only| C[Fenced reduce only close]
  C -->|actual fill and flat proof| L[Ledger and ownership release]
```

Diagrams describe contracts and failure boundaries. Their presence does not
replace tests, independent review, exact CI SHA or authenticated runtime checks.
