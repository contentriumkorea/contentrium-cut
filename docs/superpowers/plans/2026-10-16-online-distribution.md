# Contentrium CUT Online Distribution Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline, followed by one independent whole-change review.

**Goal:** Publish a small latest-discovering installer and clearly separated extractable distribution.

**Architecture:** Reuse existing signed update validation and full Setup. Add a small downloader entry point and separate signed distribution metadata, preserving all legacy update asset names.

**Tech Stack:** Python 3.12, Tk, cryptography Ed25519, PyInstaller, GitHub CLI.

**Spec:** `docs/superpowers/specs/2026-10-16-online-distribution-design.md`

## Global Constraints

- Online installer <=20MiB; official contentriumkorea/contentrium-cut only; existing trust key unchanged.
- Release title Contentrium CUT; previous published files, v0.1.1 migration, user installation and PAUSED automation preserved.
- Extractable ZIP requires Premiere panel registration; never advertise complete registration-free portability.
- No computer use, browser automation, Premiere operation, live PC reinstall, tokens or media upload.

## Review Focus

- Download/launch race: changed or withdrawn release cannot execute a different Setup.
- Partial files/cancel: no execute or overwrite of caller files.
- Invalid signed product/asset identity: reject before payload execution.
- Packaging excludes runtime modules without breaking actual frozen discovery/download.
- Existing clients accept unchanged update manifest; distribution metadata signs additional assets separately.

### Task 1: Small online installer

Files: `companion/contentrium_cut/online_installer.py`, `tools/online_install_entry.py`, `tools/build_online_setup.py`, `tests/test_online_installer.py`.

- [x] Write/run failing behavior tests against existing signed fixture transport for latest discovery, exact-tag command, tampering/withdrawal/cancel/overwrite; implement minimal downloader and GUI.
- [x] Build function produces a frozen downloader without AI/runtime modules and refuses existing outputs or >20MiB.
- [x] Run related tests and commit.

### Task 2: Distribution packaging and publication

Files: `tools/distribution.py`, `tools/publish_release.py`, `tests/test_distribution.py`, `tests/test_publisher.py`, README/version descriptors.

- [x] Write/run failing tests for portable contents, publication order, separate signed metadata, resume immutability and labels.
- [x] Package extractable runtime+CCX+Korean guide; publish legacy five assets first, extra two assets and signed distribution metadata before public latest.
- [x] Update version0.1.108 and Korean download guidance. Run related tests and commit.

### Task 3: Verify and publish

- [x] Whole Python/Node tests; independent review with security/failure focus; fix material findings with failing regressions.
- [x] Build runtime, CCX, full Setup, small installer; run package probes and distribution verification without changing live installation.
- [x] Push reviewed source, publish v0.1.108 title Contentrium CUT, verify anonymous current/previous discovery and actual downloads/signatures/hashes including v0.1.1.
- [x] Record evidence, limitations, final version and next steps. Keep automation PAUSED.
