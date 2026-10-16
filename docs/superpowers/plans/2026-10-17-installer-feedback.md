# Installer Feedback Implementation Plan

> Use superpowers:executing-plans inline and requesting-code-review for one independent review.

**Goal:** Real transfer progress, coherent installer UI without feather, and durable actionable child failures.

**Spec:** `docs/superpowers/specs/2026-10-17-installer-feedback-design.md`

**Architecture:** Optional streaming progress callbacks; one shared Tk view; a small structured Setup outcome/reporting module and guarded entry wrapper. Retain existing installation/activation contracts.

**Tech Stack:** Python/Tk, existing signed GitHub transport, Windows frozen EXEs.

## Global constraints

- No CUA/browser/Premiere operation or live reinstall; automation stays PAUSED.
- No invented download percentage or native completion; no trust/root/host-exit bypass.
- Existing public files/key/user data/v0.1.1 preserved; publish 0.1.109 Contentrium CUT.

## Review focus

- Cancellation during transfer, verification, and child handoff.
- Early import/root failures and nonzero child with no result.
- Malformed child reports never imply success; private values never logged.
- Progress monotonic for a transfer; reset across distinct files; no fake 100% during registration.
- Frozen icon/resource inclusion and isolated child environment without live installation.

### Task 1: Reporting and transfer boundary

Files: updater.py/dependencies.py/online_installer.py, new installer_reporting.py and tests.
- [x] RED tests for byte progress, child failure/result, environment isolation, diagnose safety/import failure.
- [x] Implement minimal optional callbacks and structured reports; run related regressions.

### Task 2: Shared UI and assets

Files: new installer_ui.py, setup_entry.py, assets, install_entry.py/online_install_entry.py/build scripts and UI tests.
- [x] RED behavior tests for progress/state/errors/copy/cancel/icon.
- [x] Implement shared mono UI, transparent title icon/custom EXE icon, Setup result/diagnose wrapper and callback wiring.
- [x] Whole Python/Node regression, independent review and material fixes.

### Task 3: Build and publish

- [x] New0.1.109 versions; frozen check/diagnose/transfer plus PE icons and source/package validation.
- [ ] Publish reviewed new release with same9 assets; anonymous discovery/signatures/actual5 downloads/pinnedv0.1.1.
- [ ] Record failures/limits/actual status and clean Git; no recurring work or live reinstall.
