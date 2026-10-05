# Contentrium CUT Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Execute continuously in this session. User explicitly authorizes implementation, local installation and three public deployments; do not ask again for a plan handoff or publication approval.

**Goal:** Implement the approved Premiere plugin, install and publish a first version, audit and improve it twice, reinstall only after the second audit, and leave the third release available for the user's update test.

**Architecture:** A thin Premiere UXP panel reads the real project and applies a validated plan to a cloned sequence. An authenticated local Companion owns durable jobs and separate analysis workers. An independent updater verifies signed GitHub release packages, interrupts all CUT work before downloads, installs a matched panel/Companion pair and preserves user data.

**Tech Stack:** UXP JavaScript/CommonJS with explicit contracts, Python 3.10+ independent packaged runtime, FFmpeg, Silero ONNX, pyannote Community-1, standard HTTP/JSON, Ed25519 signed update manifests, CCX/Adobe UPIA and a Windows installer.

**Spec:** ../specs/2026-10-05-premiere-speaker-cut-design.md and its five details.

## Global Constraints

- Product/UI/installer/Release title: `Contentrium CUT`; GitHub repository: `contentriumkorea/contentrium-cut`.
- Preserve original media and sequence; change video visibility/cuts on a new editable sequence only; preserve audio and supported effects.
- Local separated microphones AND mixed audio; preserve overlap, review unknown, block confirmed unmapped speakers and missing audio/model failure.
- M=2.0s, B=0.6s, O=0.8s; rational FPS and half-open integer frames; tick values are decimal strings.
- Check updates on panel open; silent if current; notify if available; click blocks new work and immediately requests cancellation of all CUT work.
- Do not terminate Premiere or unrelated processes. Current short host transaction must return before another can run; installation waits for safe host exit.
- Keep data/models/cache outside versioned application files; never embed GitHub PAT, model token or signing private key in source/packages.
- Round 1: version 0.1.0, actual install and public release. Round 2: audit/spec/fixes, version 0.1.1, reinstall and release. Round 3: audit/spec/fixes, version 0.1.2, release WITHOUT local reinstall/activation.
- Do not perform the user's final 0.1.1→0.1.2 update. Test its check/download/signature paths in isolation; leave local 0.1.1 active.
- Real host evidence is required separately from simulated tests. Missing model access or source evaluation data is recorded explicitly; no mock is presented as completed real validation.
- Software/internal filenames retain functional names, without delivery timestamps.

## Review Focus

- Host clone/trim may also clone linked audio or change keyframes; test independent audio, linked media, source/effect fingerprints and original invariants before allowing apply.
- Long files, rational FPS, split sources and indirect sync paths can drift; test exact time conversion, coverage gaps and inconsistent sync cycles.
- Late worker completion and multi-panel update races can falsely commit results; test admission epoch, cancel acknowledgement and durable terminal state.
- Network failure, corrupt signed manifests, interrupted installation and mismatched versions must not appear as success; test staging/rollback and real host activation.
- Gated model setup, unseen speakers and unknown audio must remain visible; test model-not-ready, missing mapping and overlap without collapsing speakers.

## Files and responsibilities

| Path | Responsibility |
| --- | --- |
| plugin/manifest.json, index.html, styles.css, main.js | Actual UXP panel and Mono Studio workflows |
| plugin/host.js | Actual Premiere reads, capability proof, clone/apply/readback, cancellation |
| companion/contentrium_cut/contract.py | Time/snapshot validation and canonical hashing |
| companion/contentrium_cut/policy.py | Deterministic edit plans and user overrides |
| companion/contentrium_cut/audio.py | PCM extraction, multi-window sync and separated microphone analysis |
| companion/contentrium_cut/models.py | Local model setup and mixed diarization adapter |
| companion/contentrium_cut/jobs.py | Durable job lifecycle, worker ownership and cancellation |
| companion/contentrium_cut/server.py | Authenticated loopback job API and panel participants |
| companion/contentrium_cut/updater.py | GitHub discovery, signature verification, quiescing and recoverable installs |
| companion/contentrium_cut/launcher.py | Independent program entry point and Windows installer/update window |
| tools/build.py, install.ps1, publish.py | Reproducible CCX/EXE/archive/signature/release assets |
| tests/ | Real contract, policy, generated media, protocol and updater regression tests |
| docs/qa/ | Per-round requirement matrix, findings, revised design and verification evidence |

## Tasks

### Task 1: Establish repository, tools and actual host feasibility

**Files:** plugin/manifest.json, plugin/host.js; tools/host-proof.js; docs/qa/host-proof.md.
**Interfaces:** `readSnapshot()` → InputSnapshot; `applyPlan(snapshot, plan, cancellation)` → ApplyReceipt; `runHostProof()` → evidence or explicit unsupported capability.
- [ ] Observe failed host proof before implementation: no plugin/adapter exists.
- [ ] Create a dedicated Git repository from existing design, baseline commit, and implementation branch.
- [ ] Create/load a minimal real UXP panel through Adobe tools; inspect real APIs in installed Premiere.
- [ ] Create owned synthetic 30fps/24s/3-camera media and test project; prove clone, video-only split, disabled switching at 180/360/540 and readback without original/audio changes.
- [ ] Record supported/blocked effects and linked configurations. Fix actual failures rather than claiming API documentation proves success.
- [ ] Commit only after actual host result or clearly recorded external blocker.

### Task 2: Time contracts and deterministic policy

**Files:** contract.py, policy.py, tests/test_contract.py, tests/test_policy.py.
**Interfaces:** `plan_edit(snapshot, analysis, mapping, policy)` → EditPlan; `validate_plan(plan, snapshot)` → validated plan or typed error.
- [ ] Write and run failing tests for exact rational/tick mapping, the approved 600-frame example, gaps, overlapping overrides, missing mapping, unknown, sustained overlap and expired delayed turns.
- [ ] Implement explicit contracts, canonical hashing, frame boundaries and camera coverage/fallback.
- [ ] Run all policy/contract cases. Expected: exact 8 intervals and seven approved transitions, zero gaps or original mutations.
- [ ] Commit and record test result.

### Task 3: Local audio, automatic sync and both recording modes

**Files:** audio.py, models.py, tests/test_audio.py, tests/test_models.py.
**Interfaces:** `sync_sources(sources, reference, fps, cancel)` → SyncPlan; `analyze_audio(mode, sources, settings, cancel)` → SpeakerAnalysis.
- [ ] Write and run failing generated-media tests for offsets, indirect overlap, drift, separate voices/bleed, duplicate channels, silence/missing input and preserved mixed overlap.
- [ ] Implement streamed/ranged FFmpeg decoding, coverage-aware source timing, distributed correlation validation and sync graph.
- [ ] Implement Silero channel VAD/bleed evidence and Community-1 local diarization with session IDs and model setup. No ASR dependency.
- [ ] Install isolated dependencies/models where user access allows; expose provider setup and meaningful MODEL_NOT_READY without automatic ToS acceptance.
- [ ] Run real VAD/sync fixtures and adapter tests; record actual model-access/quality boundaries separately.
- [ ] Commit and record result.

### Task 4: Durable jobs and authenticated local control

**Files:** jobs.py, server.py, tests/test_jobs.py, tests/test_server.py.
**Interfaces:** `/v1/health`, `/v1/jobs/...` and registered panel participants; durable requestId/revision/updateEpoch.
- [ ] Write and run failing tests for loopback/auth/source allowlisting, idempotence, cancellation and late worker results.
- [ ] Implement jobs, independent worker lifetime, atomic JSON persistence and approved API paths.
- [ ] Verify process ownership before stopping child jobs, preserve completed artifacts and partial ApplyReceipt.
- [ ] Run server integration tests including real child cancellation and restart recovery; commit.

### Task 5: Mono Studio panel and complete Premiere workflows

**Files:** plugin/main.js, index.html, styles.css, host.js; tests/test_panel.cjs; docs/qa/ui.md.
**Interfaces:** read sources/sequence, sync, analyze, speaker-camera mapping, policy/overrides, review, plan, apply, cancel, model/update management.
- [ ] Define failing state tests for disabled prerequisites, mapping/unknown separation and update gating.
- [ ] Implement narrow responsive Mono Studio panel with real progress and all prerequisite/review/failure states.
- [ ] Wire current-sequence and project-clips workflows, source channels, sync review, speaker samples/mapping, policy and interval corrections.
- [ ] Apply only after supported host proof, snapshot revalidation and plan validation; read back actual sequence and preserve originals.
- [ ] Check actual Premiere panel at narrow/wide sizes and real workflows; commit.

### Task 6: Independent updater, packaging and first deployment

**Files:** updater.py, launcher.py, tools/build.py, tools/publish.py, install.ps1; tests/test_updater.py.
**Interfaces:** `/v1/updates/check|state|start|participants/ack|cancel`; signed manifest with exact asset IDs/hashes; versioned application install.
- [ ] Write and run failing update tests for SemVer, API headers, manifests/paths, immediate gate/cancel, state persistence, corrupt download and rollback.
- [ ] Implement independent manager/window, authenticated participants and safe host-exit installation; maintain data and fixed plugin ID.
- [ ] Build matched panel/Companion and installer; generate protected signing key outside repo, embed public key and sign update manifest.
- [ ] Install 0.1.0 through official Adobe path, confirm actual loaded panel and Companion.
- [ ] Publish named repository and first Release `Contentrium CUT`/v0.1.0; verify anonymous production metadata, hashes and asset downloads.
- [ ] Commit and archive evidence, including any explicit unverified requirement.

### Task 7: Second audit, revised design, fixes, reinstall and deployment

**Files:** docs/qa/round-2.md and round-2-design.md; affected production files and meaningful regression tests.
- [ ] Request a fresh independent whole-code/spec review as required by executing-plans; combine it with requirement-by-requirement and actual installed-host inspection.
- [ ] Record every missing/partial/untested item and its implementation or external prerequisite. Produce revised design before fixes.
- [ ] Write reproducing failing tests for important findings, fix and run the whole relevant suite/host checks.
- [ ] Build/reinstall matched 0.1.1; confirm actual activation and original/data invariants.
- [ ] Publish v0.1.1 and verify public update metadata and assets; archive matrix, findings and evidence.

### Task 8: Third audit, revised design, fixes and deployment only

**Files:** docs/qa/round-3.md and round-3-design.md; affected files/regression tests; final-user-update-test.md.
- [ ] Perform another independent requirement review and local installed 0.1.1 inspection; save revised design before fixes.
- [ ] Fix important findings with failing-to-passing tests and final relevant verification.
- [ ] Build/publish 0.1.2 without installing or activating it locally. Verify actual public metadata/signatures/downloads with isolated checks.
- [ ] Prove local registered plugin and active Companion remain 0.1.1; prove latest remote is 0.1.2.
- [ ] Deliver exact release links, install/open instructions and manual update test steps with remaining implementation/verification limits.

## Execution rulings

- The user's explicit implementation/install/deploy instruction overrides the skill's optional plan approval handoff; continue without another confirmation.
- The project is a new dedicated directory without an existing repository, so establish an isolated implementation branch in place rather than duplicate unrelated workspace files.
- Review rounds are substantive defect/spec audits. Version bumps alone do not constitute implementation or verification.
