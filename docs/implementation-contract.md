# Implementation interfaces — Contentrium CUT

This pins inter-module integration values; the approved design remains authoritative. User authorizes three rounds of implementation/install/deploy, with no local install in round 3. Do not publish or install from worker agents.

## Shared data

- `schemaVersion: 1`, `appVersion: "0.1.0"` initially. Tick strings are decimal integers; Premiere ticks per second = `254016000000`. FPS `{num,den}`. Segments `[startFrame,endFrame)` integer frames.
- `InputSnapshot`: `{schemaVersion, projectRef, sequenceRef, projectName, sequenceName, fps, range:{startFrame,endFrame}, tracks, clips, sources, supportFlags, snapshotHash}`.
- A track is `{trackRef, mediaType:"video"|"audio", index, name, muted, protected}`. Camera tracks are explicit user choices; all others are protected.
- `SourceAsset`: `{assetId, canonicalPath, size?,mtime?,fingerprint?,streams?}`. Registered paths come only from Premiere snapshot/source selection; backend revalidates real path/file metadata.
- `SourceClip`: `{instanceKey, assetId, projectItemRef, trackRef, mediaType, startTicks,endTicks,inTicks,outTicks,speed,disabled,channelMap,effectFingerprint,supportFlags}`. A camera binding points to these instances.
- `SpeakerAnalysis`: `{schemaVersion, modelRevision, intervals:[{startFrame,endFrame,speakers:[sessionSpeakerId],unknown:false,reason?}], sessionSpeakerIds, reviews, validAudioRanges}`. Speaker intervals MAY overlap; normalization must preserve actual simultaneous voices. `unknown` is a flag, not a fake speaker.
- `SpeakerMapping` for policy: `{speakers:{"A":"CA","B":"CB"}, cameras:[{cameraId,role:"speaker"|"wide"|"two-shot"|"reserve",coveredSpeakers,clips:[{instanceKey,startFrame,endFrame}],priority?}], startCameraId:"W", fallbackOrder:[]}`. Camera clips refer to source instances in snapshot.
- `Policy`: `{minShot:2.0,shortTurn:0.6,suppressShort:true,overlap:0.8,overrides:[{startFrame,endFrame,cameraId}]}`; durations in decimal seconds interpreted exactly using rational FPS.
- `EditPlan`: `{schemaVersion,snapshotHash,segments:[{startFrame,endFrame,cameraId,sourceClipInstanceKey,reason}],reviews,planHash}`. No invalid coverage/length/gaps. Typed blocking error has `code` and details; do not report errors as silence.
- `ApplyReceipt`: `{schemaVersion,appVersion,jobId,planHash,outputSequenceRef,batches,readback,sourceUnchanged,status}`; completed only after real host comparison.

## Python integration

- Package path `companion/contentrium_cut/`. Tests run `PYTHONPATH=companion python -m unittest discover -s tests` (PowerShell `$env:PYTHONPATH='companion'`). Use stdlib unittest where sufficient.
- `contract.CutError(code: str, message: str, details=None)` exposes code/message/details. `canonical_hash(value)` excludes nothing implicitly. `frame_ticks(frame, fps)` returns exact tick string; `ticks_frame(ticks,fps)` nearest half-forward integer frame.
- `policy.plan_edit(snapshot, analysis, mapping, policy)` returns EditPlan. `policy.validate_plan(plan,snapshot,mapping=None)` returns plan or CutError. No Premiere object or file edits.
- `audio.sync_sources(sources, reference, fps, cancel=None)` accepts source descriptors `{assetId,path,streamIndex:0,channelIndex:0,offsetSeconds?:0}`; reference assetId. Returns per-source offsets and evidence/reviews. Decode/correlation can use locally installed NumPy/SciPy and configured FFmpeg.
- `audio.analyze_audio(mode,sources,settings,cancel=None)` returns SpeakerAnalysis; settings include fps/range/offsets/channels/speakerCount/modelRoot/ffmpeg. `mode` is `separate` or `mixed`.
- Cancel callable raises `CutError("CANCELED",...)` when signaled, and must be checked between ranges/expensive stages. Parent terminates only confirmed owned worker if bounded cooperative cancellation fails.
- Models must be local and provider terms must not be silently accepted. Model tokens belong in DPAPI-protected user storage, never source/project/logs. Absence is MODEL_NOT_READY; source missing is MISSING_AUDIO.

## Updating integration

- Updater manager uses fixed `contentriumkorea/contentrium-cut` API; productId `com.contentrium.cut`, releaseTitle `Contentrium CUT`; no suffix repository.
- App version and public signing key are supplied by `config.json` in installed bundle. Private key generated outside repository by build/publish tooling.
- Methods anticipated: `UpdateManager.check()`, `.state()`, `.start(candidate_id,manifest_digest,request_id)`, `.ack(participant_id,epoch,receipt)`, `.cancel()`; constructor accepts root directory, public key, current version, `stop_all(epoch)` callback and installation hooks, with test injection for network/installer.
- Update manager runs independently of worker cancellation; start must latch gate/epoch and invoke stop_all before download. All registered participants must acknowledge or have actually exited before replacement.
- Signature contract: Ed25519 detached signature over exact manifest UTF-8 bytes; JSON signature wrapper `{algorithm:"Ed25519",keyId,signature:<base64>}`. Manifest assets only installation payloads, not itself/signature. Asset fields `{role,assetId,name,size,sha256}`. Download via GitHub returned HTTPS asset URL; allowlisted GitHub/CDN redirects only.
- Manifest fields: schemaVersion/productId/displayName/appVersion/channel/releaseId/tag/builtAt/platform/architecture/panelVersion/companionVersion/bundleId/updateProtocolRange/minUpdaterVersion/dataSchemaFrom/dataSchemaTo/migrationId/assets/modelCompatibility/releaseNotes/signingKeyId.
- Data root `%LOCALAPPDATA%/Contentrium CUT/`; versioned app files separate from data/models/cache/updates. Updater does not kill Premiere. Actual active plugin+Companion must both match before COMPLETE. Keep previous verified install for rollback.

## Implementation ownership

Worker agents edit only assigned modules/tests/reports. Do not edit package.json, requirements, global config, plugin, build/install scripts, or other agents' modules. Send dependency requests to the coordinator. Do not spawn subagents or perform Git commit/index changes while parallel work is active; coordinator integrates and commits each reviewed scope.
