# Full remediation execution checklist

Candidate scope: the full committed `origin/main` delta plus local remediation.
Production mutation gates remain closed. A checkmark describes evidence obtained,
not readiness to trade.

## Completed evidence

- [x] Independent baseline execution/surfaces audit and 37-finding inventory.
- [x] Isolated credential-free/no-network workspace and real disposable PostgreSQL.
- [x] Native MARKET execute-price compatibility, actual fill truth and separate protection status.
- [x] GET-only startup owned-protection recovery, orphan/manual inventory admission veto.
- [x] Durable source-age admission and final ENTRY veto after async leverage setup.
- [x] Final POST regression mutation: baseline passes, removed guard fails, restored passes.
- [x] UNKNOWN/SUBMITTING ambiguity keeps financial commitments despite source expiry.
- [x] Real production-v18 backup clone upgrade through candidate migration 23 and replay.
- [x] Historical financial table hashes preserved; four consumed legacy bindings quarantined.
- [x] Cross-container runtime health: private PIDs, per-worker tmpfs, web RO metadata/socket.
- [x] Actual stop/death/stale/restart/generation-replay proof and isolated-resource cleanup.
- [x] Whole candidate secret scan reviewed; only synthetic fixtures/placeholders/nonsecrets found.
- [x] Wheel/sdist build and shell syntax checks.

## Open release gates

- [x] Complete audit-only edit revocation of older known-unsent source revisions.
- [x] Final verified-close lifecycle wiring, realPG release/admission races and uncertain-close refusal.
- [ ] Close independent-review blockers: final kill-latch race, cross-UID media handoff and parent-process credential isolation.
- [ ] Freeze exact source tree; rerun entire regression with no failures/errors/skips.
- [ ] Same-tree Ruff lint/format, mypy, compile, package/container and Compose checks.
- [ ] Independent execution/risk and surfaces/security final explicit approval.
- [ ] Stage intended paths only; verified commit/push and exact-SHA CI.
- [ ] PR merge to main with remote main SHA readback.
- [ ] Separately gated production image/schema rollout and runtime/account readback.

## Latest full snapshot

Intermediate snapshot: 1,576 tests, zero failures/errors/skips; Ruff lint/format,
strict mypy and compilation all passed. The freeze check correctly rejected this
as final evidence because three intake edit-revocation files changed while the
suite ran. A same-tree rerun is required after review remediation freezes.
Independent review additionally withheld approval for the final kill-latch race,
cross-UID source-image handoff and parent-process credential isolation.

The actual container proof ran on this host with SELinux disabled and no AppArmor
profile. It does not establish enforcing-LSM compatibility or production deployment.
No source/credential/provider/LIVE state changes are implied by these local checks.
