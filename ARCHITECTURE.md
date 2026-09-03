# Architecture

`telegram intake -> canonical analysis -> transactional dispatch fan-out -> isolated exchange workers`

The development implementation is deliberately PAPER-only. It models durable IDs, safety validation, deterministic sizing, independent venue dispatches, operator command parsing, and a read-only FastAPI dashboard. Exchange execution adapters remain fake until public-contract and credentialed PAPER gates are proven.
