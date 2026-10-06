# Contentrium CUT Single Panel Implementation Plan

> Continue the already authorized implementation using superpowers:subagent-driven-development for scoped core work and root-owned panel integration. This addendum uses docs/qa/execution-ledger.md and does not restart completed work.

**Goal:** Deliver the MVX-inspired panel and invisible local startup in 0.1.1, then audit and publish 0.1.2 without installing it locally.

**Architecture:** One UXP editing surface, hidden per-user runtime supervisor, automatically authenticated local service, durable worker/apply/update boundaries.

**Tech stack:** UXP/CommonJS, Windows Python runtime/installer, signed GitHub releases.

**Spec:** ../specs/2026-10-06-contentrium-cut-single-panel-design.md and all original preservation contracts.

## Work and evidence

- [ ] P3 — Replace index.html/style.css with compact staged controls, view.js navigation, settings and fixed activity/action area. Keep real V/A identities, focus and honest empty/error states. Verify four screens at 320/420px in a QA-only preview, then installed UXP. Do not ship preview fixtures.
- [ ] B1 — Add private installation bootstrap/challenge-response and hidden runtime lifecycle. Remove normal GUI/manual pair/open-companion flows. Pin root across packaged environments. Test replay, forged proof, missing bootstrap, secrets, bounded respawn and updater ownership; verify native hidden startup. A resident supervisor must be versioned so the stable Launcher can be replaced.
- [ ] S2 — Complete durable apply journal/service admission, selected-input ffprobe capabilities, correction/preview/resource/model routes and signed Launcher replacement. Use the round-2 service brief with B1. Test stale bundle, wrong epoch, lost receipt, restart uncertainty and launcher rollback.
- [ ] B2 — Complete the normal fresh-installer path for automatic installed UXP storage receipt collection, pending/resume and verified bundled Silero preparation. Shipped Setup must not require a manual mapping-receipt argument. First panel opening may complete installation through its own data folder; no separate control app or connection code. Review and isolated tests precede the native Q2 gate.
- [ ] P4 — Integrate automatic sequence read, scoped settings, invalidation, stream/channel/manual/timecode sync, correction history, sample listening, model setup and cache controls. Wire mutation.js beforeBatch/afterBatch/onResult to S2. A failed sequence read must leave no usable stale plan.
- [ ] M1 — Prepare and review a headless source maintenance bridge for the legacy 0.1.0 migration after S2/B2. Use the exact signed target and preserved updater journal, complete B2 provisioning for the legacy install that lacks private bootstrap, then use B1's native-only activation handoff. Replace obsolete source GUI/pairing instructions; do not run the bridge until native work is permitted.
- [ ] Q2 — Finish native long fixture/keyframe/audio/selected-input proofs and independent review/full regression. Build/probe/sign/publish 0.1.1, perform recorded source-assisted 0.1.0 recovery, verify installed panel, auto-connect, native edits and new stable Launcher. Record limitations separately.
- [ ] Q3 — Audit final 0.1.1, write round-3 design, implement actual fixes, test/sign/publish 0.1.2. Validate public discovery/signature/hash in isolation and leave local 0.1.1. Never start the live final update.

## Interfaces and review focus

| Tasks | Shared boundary | Ruling |
|---|---|---|
| P3/P4 | DOM IDs/view state | Preserve functional IDs, explicitly replace obsolete pairing listeners |
| B1/S2 | authentication/startup/handoff | One owner for port/mutex; supervisor cannot spawn around update ownership |
| S2/P4 | jobs/correction/model/apply | Pin exact requests and receipts; no client-supplied native proof |
| audio/S2 | coordinator/cache APIs | Follow completed audio report; no duplicated correction logic |
| Q2/Q3 | product version/install root | Local 0.1.1, public 0.1.2; QA harness version is unrelated |

Review accidental visible app launch, auth downgrade, stale identities, native interruption, old-launcher activation, and inaccessible narrow controls. Resolve defects affecting these gates before completion claims.

## Source checkpoint — 2026-10-06

The scoped source tasks and the final whole-branch source review are approved after the single FR1–FR5 fix wave. Final guarded regression: Python 465/465 and Node 141/141, no executed skips; 13 named native/real-model gates remain explicitly excluded. Detailed findings, rulings, failed-run history and final source verdict are recorded in docs/qa/execution-ledger.md and docs/qa/round-2.md. Preserve the existing SDD evidence and reviewed uncommitted source; do not restart completed source tasks.

The checklist above deliberately remains open where it includes installed/native acceptance. The user's repeated Computer Use prohibition is still active. Actual installed UXP, native editing/preservation, model quality, Windows lifetime/ACL/pipe behavior, frozen packaging, source-assisted migration and Q2/Q3 delivery remain next-stage gates. Metadata is still 0.1.0; neither final local 0.1.1 nor public 0.1.2 has been achieved by this source checkpoint. No final update is to be installed locally.
