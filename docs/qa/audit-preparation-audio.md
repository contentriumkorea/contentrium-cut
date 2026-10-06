# Audio audit preparation — 2026-10-05

Status: preparation for the formal second audit. This is **not** the completed second audit, a post-release verification, or permission to claim the approved design is fully implemented. First live publication/installation and the root agent's release announcement remain the formal audit gate.

Scope: current `companion/contentrium_cut/audio.py`, `coordinator.py`, `jobs.py`, and `models.py`, compared with `docs/superpowers/specs/2026-10-05-premiere-speaker-cut-design.md` and `docs/superpowers/specs/details/audio-sync-and-speakers.md`. Service/panel code was consulted only to establish whether missing engine capabilities have a usable caller path. No production code, dependencies, Git state, installed application, or host UI was changed. The only persistent review output is this document. Temporary reproduction files/processes were removed.

## Verdict and priorities

No Critical finding was confirmed in this scope. Four Important items have direct reproduction evidence: clip-instance collapse, split-file speaker rejection, unchecked cached output, and worker-descendant quiescence. Mixed-speaker correction is an Important approved requirement without an implementation path. Resource/chunk handling and manual/channel-aware sync are additional design gaps, not evidence of silently successful unsupported processing.

| ID | Priority/type | Current code | Trigger and consequence | Implementable fix |
| --- | --- | --- | --- | --- |
| A1 | Important / defect | `coordinator.py:39–46`; `audio.py:325,351–354` | Select two distinct instances of one asset with equal `startTicks-inTicks` but different source ranges. `sources[assetId]` retains only the last instance, while both channel assignments remain. The earlier placement disappears and both assignments decode the last range. Different offsets are explicitly rejected instead. | Key analysis inputs by stable clip-instance/channel identity, retaining asset identity for media fingerprints. Decode or reuse source evidence per range, then map each instance independently onto the session. Do not merely remove the differing-offset guard. |
| A2 | Important / functional gap | `audio.py:348–349`; `_separate` speaker-keyed rows at `audio.py:245–255` | Map two split recordings or a microphone change to the same confirmed person. Analysis rejects `INVALID_AUDIO_INPUT` because speaker IDs must be unique per channel. Renaming the second file to a different person works around the guard but breaks the session identity contract. | Keep recording/channel/instance IDs separate from session speaker IDs. Aggregate valid speech evidence from multiple segments under one confirmed session ID; preserve genuine gaps, overlap, and per-segment calibration. Removing only the uniqueness check would leave speaker-keyed row overwrites. |
| A3 | Important / defect | `jobs.py:48–54,109–116` | A cache file at the correctly derived input key is changed to different syntactically valid JSON. `_worker` returns `ok=True` without result integrity or output-schema validation. A structurally valid altered speaker result can influence a new edit plan. Input hashing does not authenticate the cached output. | Persist a versioned cache envelope binding input key, engine/result schema, model identity, result, and result digest. Validate digest, required fields, finite bounded intervals, identity/coverage consistency, and expected kind before returning a hit. Reject/quarantine invalid legacy or corrupt entries and recompute. This protects accidental/stale output corruption; it is not a claim that a same-user attacker cannot rewrite both result and digest. |
| A4 | Important / defect | `jobs.py:146–147,165–179`; `audio.py:29–50` | An owned worker is unresponsive while its owned child is alive. After three seconds the monitor terminates only the worker, removes it, and declares quiescence while the child continues. Windows parent termination bypasses the audio runner's `finally`. Updater/handoff can therefore proceed with decoder descendants still running. | Establish a strict Windows process scope before worker execution, include descendant decoders, prohibit escape/breakaway, and keep the scope handle alive through shutdown. Graceful cancel first; after timeout terminate only the verified owned scope. Quiescence must prove that its active processes are zero before release/reopen/handoff. Preserve journal/controller processes outside that scope. |
| A5 | Important / missing requirement | `audio.py:340–359`; `coordinator.py:61–64`; panel `main.js:34`; service job/plan routes | Mixed diarization emits local-to-session labels and camera assignment, but there is no solo-example extraction/listening, same-person ID merge, interval speaker reassignment, raw-versus-corrected analysis persistence, or correction revision path. Assigning duplicate IDs to one camera is not an identity merge; a mismerged speaker cannot be corrected by a single ID→camera mapping. | Add session-owned immutable raw analysis and explicit versioned correction operations. Generate several separated solo examples per ID with source/sample provenance. Provide merge and bounded interval reassignment, validate overlaps/gaps and scope, invalidate plans after correction, and retain history separately from model output. |

Contract anchors: main design `:140,207–213,394`; audio detail `:27,75,89–99,103,125–127`. A1/A2 directly contradict maintaining different clip placements and the same person across split recordings. A4 contradicts verified owned worker **and decoder** shutdown. A5 is expressly required, not a requested quality improvement.

## Reproduction evidence

All commands used the existing Python 3.12 build environment with bytecode writes disabled. Paths in outputs are intentionally omitted here.

### A1 — equal offset, different source trims

Used the existing `CoordinatorTests.fixture()` and a deep-copied second clip, then recomputed the genuine snapshot hash and bound it normally. The first selected instance starts at 2 s, reads source 1–11 s; the second starts at 12 s, reads source 11–21 s. Both have offset 1 s. Assigned separate IDs A and B, channel 0, to demonstrate two distinct channel entries with one descriptor.

Observed payload:

```json
{"selectedClipCount":2,"sourceCount":1,"sourceStartSeconds":11.0,"durationSeconds":10.0,"sequenceStartSeconds":12.0,"channels":[{"assetId":"a","speakerId":"A","channelIndex":0},{"assetId":"a","speakerId":"B","channelIndex":0}]}
```

This is an actual Coordinator boundary reproduction, not a model-quality inference. The existing test `test_repeated_asset_different_timeline_offsets_requires_distinct_processing` proves the explicit different-offset rejection but does not cover silent equal-offset range collapse.

Acceptance regression: two same-asset instances with equal offset/different trims; different offsets; same-person split files; duplicate selected instance rejection; two channels of the same multichannel asset; disjoint/overlapping ranges; source-origin provenance; original gaps. Results must retain every selected instance and remain deterministic regardless of selection order.

### A2 — one person, two recording segments

Called `analyze_audio('separate', ...)` with two distinct asset IDs and channels both assigned session ID A. Patched only `_probe` to bypass irrelevant media inspection and reach the production identity validator; no model output was fabricated.

Observed: `INVALID_AUDIO_INPUT: Separate microphones require distinct session speaker IDs.` This directly establishes the input contract limitation. It does not establish actual Silero accuracy on split recordings.

Acceptance regression: two sequential files with the same session ID A should produce A's union of valid speech without converting the source gap to silence or inventing speaker B. Use actual FFmpeg trims, a real Silero sample, and synthetic deterministic evidence for overlap/calibration boundary cases.

### A3 — valid JSON output substitution

Derived the production cache key using `_fingerprints` and `canonical_hash({kind,payload,files,engineSchema:1})`, wrote a JSON speaker result at `<key>.json`, and called the real `_worker`. The direct boundary fixture intentionally did not invoke inference; it isolates the cache acceptance branch.

Observed: `ok=True`, matching `cacheKey`, and the substituted `FORGED` speaker interval unchanged. Even incomplete output passed the branch. The issue is lack of output validation/integrity; no source-key collision was claimed.

Acceptance regression: one valid completed cache hit, altered result bytes, malformed shape, mismatched key/model/schema, nonfinite or negative intervals, malformed coverage, interrupted/pending entry, and recomputation after invalidation. Existing stale-epoch cache-promotion tests must remain green.

### A4 — forced worker descendant on actual Windows

Created an owned multiprocessing worker using `JobManager.context`. That worker launched an actual child process and deliberately ignored cooperative cancellation. Registered the real worker/event/queue through the same manager monitor structures; invoked `stop_all(1)` and waited up to six seconds. The child represented a decoder blocked beyond the grace period. It was not an actual stalled FFmpeg invocation.

Observed after approximately three seconds:

```json
{"quiescent":true,"workerRegistered":false,"childAlive":true,"jobStatus":"canceled"}
```

The child PID was received from its parent; a native Windows handle was opened before cancellation and its executable identity verified. The same pinned native handle was used to verify continued execution and clean up the exact child. No name-wide process kill, unrelated process termination, or leaked reproduction process occurred.

Acceptance regression: real Windows worker + child + grandchild remain inside the scope; cooperative cancellation drains normally; stubborn child is terminated after grace; no descendant survives `quiescent=True`; queued/late results never promote; no new worker escapes before scope assignment; controller/update processes remain alive. Also exercise actual FFmpeg cancellation, including a deliberately blocked decoder, rather than treating the generic child fixture as complete decoder proof.

## Remaining exact design gaps

| Requirement | Current behavior/evidence | Priority and follow-up |
| --- | --- | --- |
| Long mixed recording/chunk identity, audio detail `:93–99` | `audio.py:331` permits one selected mixed source; `:335–336` explicitly blocks recordings over 7,200 s. No chunk descriptors, overlap ownership, ambiguous cross-chunk linking, or editable linking table exists. Whole-session labels are correctly session-local rather than silently reused across chunks. | Important capability gap for the approved long-session workflow. Implement bounded chunks and explicit link evidence/review, integrated with A5; retain the current honest guard until ready. |
| Resource admission, audio detail `:117`; main `:367–369` | Decode uses disk-backed PCM, but no disk-space/decoded-size/model-memory admission check exists. `models.py:172–176` reads the entire mixed WAV into int16 RAM and creates a full float32 waveform. At the two-hour limit the int16 array is 230.4 MB and the final float32 array 460.8 MB, before conversion temporaries, model state, or inference. The length guard runs after decode. No GPU selection/OOM CPU retry contract or cache budget/eviction setting exists in these modules. | Important predictable-resource gap; no real OOM was induced. Probe and budget before decode/model load, bounded PCM ingestion, explicit resource error/retry, versioned cache budget excluding corrections/receipts. CPU default alone is not a GPU failure. |
| Reference/stream/channel selection and manual/timecode recovery, main `:150–161`; audio detail §5 | `coordinator.py:53–60` provides only common-sound sources and hard-codes channel 0; service options have no sync stream/channel, manual correspondence/offset, or validated timecode input. Unresolved sources are correctly blocked by `sync_plan`. Users can separately align media in Premiere, but no verified manual-sync result enters this pipeline. | Important approved-workflow gap. Add scoped per-source sync stream/channel configuration and explicit manual confirmation/exclusion with provenance. Implement timecode only with clock/FPS/DF/date/reset validation; do not remove unresolved guards. |
| Artifact revisions and durable corrections, main `:207`; audio detail `:103` | Job envelope supplies job ID and creation time; engine analysis supplies schema/model revision, source samples and frames. Separate input/analysis/mapping/correction revisions and all mandated identity fields are not consistently present. Plans are session-memory entries; no correction artifact exists. | Minor bookkeeping gap independently; Important as a prerequisite for A5 and reliable correction invalidation. Define/persist versioned artifacts without duplicating or overwriting raw evidence. |
| Calibration changing across microphone segments, audio detail `:77` | Current calibration derives per-speaker patterns; there is no explicit validity span/model for microphone replacement or changed automatic gain. Splits cannot currently enter under one ID (A2). | Extend A2 with segment-bound calibration and reviews when patterns stop explaining observed channels. Not a confirmed incorrect real-world classification from this review. |

## Verification performed and limits

Command: `.build-venv/Scripts/python.exe -m unittest test_audio test_models test_coordinator test_jobs`, with `PYTHONPATH=companion;tests`, `PYTHONDONTWRITEBYTECODE=1`, and the locally available real Silero model root.

Result: **49/49 passed in 7.234 seconds**. The actual Silero/Korean SAPI test reported model revision `1e261b036686cd0017d500ee96acd1c4ba572a9d`, 204,607 samples, 192 speech frames, and seven output intervals. These green tests do not cover A1's silent collapse, A2's intended multi-segment identity, A3's output substitution, A4's descendant lifetime, or A5's missing product workflow.

Already-supported behavior seen in code/tests: actual clip trims and session origins for a single instance; disk-backed source decode; normal mixed diarization overlap rather than exclusive output; exact overlap participant sweeps; source gaps not automatically relabeled as silence; source content hashes and model/settings in cache keys; changed-source detection; canceled/stale epoch results blocked from cache completion; duplicate polarity-aware channels and incomplete calibration reviews; sync drift/graph conflict/cumulative uncertainty reviews and unresolved apply blocking; pinned local model manifests and model hash checks. These observations are limited to their existing evidence and are not blanket Adobe/native or diarization quality proof.

## Explicitly set aside

- First public release, first installed production app, installed panel workflow, restart/update/handoff and actual Premiere playback: root owns the release gate; this document precedes it.
- Our own `plugin/sync.js`: excluded from independent implementation review. Its existence does not establish the missing audio/session requirements above.
- Real Community-1 download, offline inference/network observation, Korean diarization quality/overlap, long-session memory/time, CUDA behavior: actual gated Community-1 weights are unavailable. Mock pipeline tests establish adapter behavior only.
- Real stalled FFmpeg descendant shutdown: generic owned Windows descendant was reproduced; an actual decoder stall remains a required regression after the scope fix.
- Real multi-hour resources and disk exhaustion: implementation omissions are visible; no resource-exhaustion experiment was run on the user's machine.
- Source file mutation immediately after hashing and malicious same-user cache rewriting with recomputed digests: not reproduced; A3 is an output consistency/integrity finding, not an OS privilege boundary claim.

No other reviewed behavior was deliberately declined. Formal second audit status remains **not started/completed pending the first live release announcement**; findings above are actionable preparation inputs for that audit and its fixes.
