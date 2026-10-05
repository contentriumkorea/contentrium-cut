"""Deterministic, side-effect-free camera planning on integer frame intervals.

Host capability evidence and file decoding belong to the snapshot producer. This
module rejects negative capability evidence and proves tick/coverage consistency;
it neither calls Premiere nor changes tracks, links, effects, audio or files.
"""
import copy
from bisect import bisect_right
from fractions import Fraction

from .contract import (CutError, SCHEMA_VERSION, canonical_hash, fps_fraction,
                       frame_ticks, integer, seconds_frames, tick_int)


def _fail(code, message, **details):
    raise CutError(code, message, details or None)


def _range(item, code='INVALID_CONTRACT', zero=False):
    start = integer(item['startFrame'], 'startFrame')
    end = integer(item['endFrame'], 'endFrame')
    if end < start or (end == start and not zero):
        _fail(code, 'Invalid half-open frame range', startFrame=start, endFrame=end)
    return start, end


def _unique(items, key):
    result = {}
    for item in items:
        name = item[key]
        if not isinstance(name, str) or not name or name in result:
            _fail('INVALID_CONTRACT', 'Missing or duplicate identifier', field=key)
        result[name] = item
    return result


def _flags(value):
    flags = value.get('supportFlags', {})
    if not isinstance(flags, dict):
        _fail('INVALID_CONTRACT', 'supportFlags must be an object')
    if flags.get('timeMappingSupported') is False:
        _fail('TIME_MAPPING_UNSUPPORTED', 'Time mapping has not passed support checks')
    for field in ['audioPreservationSupported', 'effectPreservationSupported', 'linkedAudioSafe']:
        if flags.get(field) is False:
            _fail('PRESERVATION_UNSUPPORTED', 'Required preservation is unsupported', field=field)


def _snapshot(snapshot):
    if integer(snapshot['schemaVersion'], 'schemaVersion') != SCHEMA_VERSION:
        _fail('INVALID_SNAPSHOT', 'Unsupported snapshot schema')
    start, end = _range(snapshot['range'], 'INVALID_SNAPSHOT')
    fps_fraction(snapshot['fps'])
    frame_ticks(start, snapshot['fps'])
    frame_ticks(end, snapshot['fps'])
    if not isinstance(snapshot['snapshotHash'], str) or not snapshot['snapshotHash']:
        _fail('INVALID_SNAPSHOT', 'Snapshot identity is required')
    _flags(snapshot)
    tracks = _unique(snapshot['tracks'], 'trackRef')
    sources = _unique(snapshot['sources'], 'assetId')
    clips = _unique(snapshot['clips'], 'instanceKey')
    return start, end, tracks, sources, clips


def _source(clip, tracks, sources):
    if clip['mediaType'] != 'video' or clip['trackRef'] not in tracks:
        _fail('SOURCE_RANGE_INVALID', 'Camera source is not a registered video clip')
    track = tracks[clip['trackRef']]
    if track['mediaType'] != 'video':
        _fail('SOURCE_RANGE_INVALID', 'Camera clip belongs to a non-video track')
    if clip['assetId'] not in sources:
        _fail('SOURCE_RANGE_INVALID', 'Camera asset is not registered')
    _flags(clip)
    try:
        if isinstance(clip['speed'], bool) or Fraction(str(clip['speed'])) != 1:
            _fail('TIME_MAPPING_UNSUPPORTED', 'Only verified normal-speed sources are supported')
    except (ValueError, ZeroDivisionError):
        _fail('TIME_MAPPING_UNSUPPORTED', 'Invalid source speed')
    first, last = tick_int(clip['startTicks']), tick_int(clip['endTicks'])
    source_in, source_out = tick_int(clip['inTicks']), tick_int(clip['outTicks'])
    if first >= last or source_in < 0 or source_out <= source_in or source_out-source_in < last-first:
        _fail('SOURCE_RANGE_INVALID', 'Source ticks do not cover the clip timeline', instanceKey=clip['instanceKey'])
    asset = sources[clip['assetId']]
    online = asset.get('online', True) is not False and asset.get('supportFlags', {}).get('online', True) is not False
    return first, last, source_in, source_out, online and not clip.get('disabled', False) and not track.get('muted', False)


def _mapping(mapping, snapshot, tracks, sources, clips):
    cameras = _unique(mapping['cameras'], 'cameraId')
    if mapping['startCameraId'] not in cameras:
        _fail('START_CAMERA_REQUIRED', 'Choose a registered start camera')
    if not isinstance(mapping['speakers'], dict):
        _fail('SPEAKER_MAPPING_REQUIRED', 'Speaker mapping must be an object')
    for speaker, camera_id in mapping['speakers'].items():
        if not isinstance(speaker, str) or not speaker or camera_id not in cameras:
            _fail('SPEAKER_MAPPING_REQUIRED', 'Speaker refers to an unregistered camera', sessionSpeakerId=speaker)
    fallback = mapping.get('fallbackOrder', [])
    if not isinstance(fallback, list) or len(set(fallback)) != len(fallback) or any(x not in cameras for x in fallback):
        _fail('INVALID_MAPPING', 'Fallback cameras must be unique registered choices')
    bindings = {}
    for camera_id, camera in cameras.items():
        if camera['role'] not in ['speaker', 'wide', 'two-shot', 'reserve']:
            _fail('INVALID_MAPPING', 'Unknown camera role', cameraId=camera_id)
        if not isinstance(camera['coveredSpeakers'], list) or any(not isinstance(s, str) for s in camera['coveredSpeakers']):
            _fail('INVALID_MAPPING', 'Camera coverage requires explicit speaker IDs')
        priority = camera.get('priority', 0)
        integer(priority, 'camera.priority')
        items = []
        for binding in camera['clips']:
            start, end = _range(binding, 'INVALID_MAPPING')
            key = binding['instanceKey']
            if key not in clips:
                _fail('SOURCE_RANGE_INVALID', 'Camera source instance is not registered', instanceKey=key)
            clip = clips[key]
            first, last, _, _, available = _source(clip, tracks, sources)
            if tracks[clip['trackRef']].get('protected', False):
                _fail('CAMERA_TRACK_PROTECTED', 'Explicit camera choices must not use protected tracks', cameraId=camera_id)
            if tick_int(frame_ticks(start, snapshot['fps'])) < first or tick_int(frame_ticks(end, snapshot['fps'])) > last:
                _fail('SOURCE_RANGE_INVALID', 'Camera binding exceeds whole-frame source coverage', instanceKey=key, startFrame=start, endFrame=end)
            if available:
                items.append((start, end, key))
        bindings[camera_id] = sorted(items)
    return cameras, bindings


def _at(bindings, camera_id, frame):
    if camera_id is None:
        return None
    choices = [key for start, end, key in bindings[camera_id] if start <= frame < end]
    return min(choices) if choices else None


def _rank(cameras, ids):
    return sorted(ids, key=lambda camera_id: (cameras[camera_id].get('priority', 0), camera_id))


def _union(ranges):
    result = []
    for start, end in sorted(ranges):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def _missing(ranges, start, end):
    cursor = start
    for first, last in _union(ranges):
        if last <= cursor:
            continue
        if first > cursor:
            return cursor, min(first, end)
        cursor = max(cursor, last)
        if cursor >= end:
            return None
    return (cursor, end) if cursor < end else None


def _analysis(analysis, mapping, start, end, overlap, reviews):
    if integer(analysis['schemaVersion'], 'schemaVersion') != SCHEMA_VERSION:
        _fail('INVALID_ANALYSIS', 'Unsupported speaker analysis schema')
    if analysis.get('error'):
        error = analysis['error']
        _fail(error.get('code', 'ANALYSIS_FAILED'), error.get('message', 'Audio analysis failed'))
    if analysis.get('status') in ['failed', 'error']:
        _fail('ANALYSIS_FAILED', 'Audio analysis failed')
    valid_ranges = [_range(r, 'INVALID_ANALYSIS') for r in analysis.get('validAudioRanges', [])]
    gap = _missing(valid_ranges, start, end)
    if gap:
        _fail('MISSING_AUDIO', 'Audio is not verified across the complete requested range', startFrame=gap[0], endFrame=gap[1])
    session_ids = analysis['sessionSpeakerIds']
    if not isinstance(session_ids, list) or any(not isinstance(s, str) or not s for s in session_ids):
        _fail('INVALID_ANALYSIS', 'Session speaker IDs must be an explicit list')
    confirmed = set()
    speaker_ranges, unknown_ranges = {}, []
    for item in analysis['intervals']:
        first, last = _range(item, 'INVALID_ANALYSIS', zero=True)
        if not isinstance(item['speakers'], list) or any(not isinstance(s, str) or not s for s in item['speakers']):
            _fail('INVALID_ANALYSIS', 'Interval speaker IDs must be strings')
        if not isinstance(item['unknown'], bool):
            _fail('INVALID_ANALYSIS', 'unknown must be an explicit boolean')
        if first < last and first < end and last > start:
            confirmed.update(item['speakers'])
        if first == last:
            reviews.append(dict(code='ZERO_FRAME_ANALYSIS', startFrame=first, endFrame=last))
            continue
        for speaker in set(item['speakers']):
            speaker_ranges.setdefault(speaker, []).append((first, last))
        if item['unknown']:
            unknown_ranges.append((first, last, item.get('reason', 'LOCAL_UNCERTAINTY')))
    missing = sorted(confirmed - set(mapping['speakers']))
    if missing:
        _fail('SPEAKER_MAPPING_REQUIRED', 'Confirmed speakers require user camera mapping', sessionSpeakerIds=missing)
    speaker_ranges = {speaker: _union(ranges) for speaker, ranges in speaker_ranges.items()}
    # End events are consumed before starts via the resulting half-open state.
    events = {start: [], end: []}
    for speaker, ranges in speaker_ranges.items():
        for first, last in ranges:
            events.setdefault(first, []).append((speaker, 1, last-first))
            events.setdefault(last, []).append((speaker, -1, 0))
    for index, (first, last, reason) in enumerate(unknown_ranges):
        token = ('unknown', index, reason)
        events.setdefault(first, []).append((token, 1, 0))
        events.setdefault(last, []).append((token, -1, 0))
    boundaries = sorted(events)
    active, unknown, atoms = {}, set(), []
    for index, frame in enumerate(boundaries[:-1]):
        for key, sign, duration in sorted(events[frame], key=lambda event: event[1]):
            if isinstance(key, tuple):
                if sign < 0:
                    unknown.discard(key)
                else:
                    unknown.add(key)
            elif sign < 0:
                active.pop(key, None)
            else:
                active[key] = duration
        atoms.append(dict(start=frame, end=boundaries[index+1], speakers=dict(active), unknown=sorted(token[2] for token in unknown), sustained=False))
    run_start = None
    for index in range(len(atoms)+1):
        is_overlap = index < len(atoms) and not atoms[index]['unknown'] and len(atoms[index]['speakers']) >= 2
        if is_overlap and run_start is None:
            run_start = index
        elif not is_overlap and run_start is not None:
            if atoms[index-1]['end'] - atoms[run_start]['start'] >= overlap:
                for atom in atoms[run_start:index]:
                    atom['sustained'] = True
            run_start = None
    # Decide continuity/length from all supplied evidence before limiting output
    # to the requested range. The range itself never shortens a known utterance.
    return [atom for atom in atoms if start <= atom['start'] < end]


def _overrides(policy, cameras, bindings, start, end):
    raw = []
    for item in policy.get('overrides', []):
        first, last = _range(item, 'INVALID_OVERRIDE')
        camera_id = item['cameraId']
        if first < start or last > end or camera_id not in cameras:
            _fail('INVALID_OVERRIDE', 'Override must be inside the target range and use a registered camera', startFrame=first, endFrame=last)
        gap = _missing([(a, b) for a, b, _ in bindings[camera_id]], first, last)
        if gap:
            _fail('OVERRIDE_COVERAGE_GAP', 'Fixed camera does not cover the complete override', cameraId=camera_id, startFrame=gap[0], endFrame=gap[1])
        raw.append((first, last, camera_id))
    raw.sort()
    for index, (first, last, camera_id) in enumerate(raw):
        for other_first, other_last, other_camera in raw[index+1:]:
            if other_first >= last:
                break
            if camera_id != other_camera:
                _fail('OVERRIDE_CONFLICT', 'Different fixed cameras overlap', startFrame=other_first, endFrame=min(last, other_last), cameraIds=sorted([camera_id, other_camera]))
    merged = []
    for camera_id in sorted(cameras):
        merged.extend((a, b, camera_id) for a, b in _union([(a, b) for a, b, c in raw if c == camera_id]))
    return sorted(merged)


def _reviews(items):
    """Merge adjacent same-cause ranges; distinct causes remain visible."""
    groups, global_reviews = {}, {}
    for item in items:
        if 'startFrame' not in item and 'endFrame' not in item:
            global_reviews[canonical_hash(item)] = item
            continue
        _range(item, zero=True)
        data = {key: value for key, value in item.items() if key not in ['startFrame', 'endFrame']}
        signature = canonical_hash(data)
        groups.setdefault(signature, (data, []))[1].append((item['startFrame'], item['endFrame']))
    result = list(global_reviews.values())
    for data, ranges in groups.values():
        result.extend(dict(data, startFrame=start, endFrame=end) for start, end in _union(ranges))
    return sorted(result, key=lambda item: ('startFrame' in item, item.get('startFrame', 0), item.get('endFrame', 0), canonical_hash(item)))


def _segment(start, end, camera_id, key, reason_codes, snapshot, clips):
    clip = clips[key]
    delta = tick_int(clip['inTicks']) - tick_int(clip['startTicks'])
    return dict(startFrame=start, endFrame=end, cameraId=camera_id,
                sourceClipInstanceKey=key,
                sourceIn=str(tick_int(frame_ticks(start, snapshot['fps'])) + delta),
                sourceOut=str(tick_int(frame_ticks(end, snapshot['fps'])) + delta),
                reason=reason_codes[0], reasonCodes=list(reason_codes))


def _plan_edit(snapshot, analysis, mapping, policy):
    start, end, tracks, sources, clips = _snapshot(snapshot)
    cameras, bindings = _mapping(mapping, snapshot, tracks, sources, clips)
    minimum = seconds_frames(policy.get('minShot', 2.0), snapshot['fps'])
    short = seconds_frames(policy.get('shortTurn', 0.6), snapshot['fps'])
    overlap = seconds_frames(policy.get('overlap', 0.8), snapshot['fps'])
    suppress = policy.get('suppressShort', True)
    if not isinstance(suppress, bool):
        _fail('INVALID_POLICY', 'Short-turn suppression must be a boolean')
    reviews = copy.deepcopy(analysis.get('reviews', []))
    atoms = _analysis(analysis, mapping, start, end, overlap, reviews)
    overrides = _overrides(policy, cameras, bindings, start, end)
    events = {start, end}
    for atom in atoms:
        events.update([atom['start'], atom['end']])
    for items in bindings.values():
        for first, last, _ in items:
            events.update(x for x in [first, last] if start <= x <= end)
    for first, last, _ in overrides:
        events.update([first, last])
    events = sorted(events)
    atom_starts = [atom['start'] for atom in atoms]
    wide = _rank(cameras, [c for c in cameras if cameras[c]['role'] == 'wide'])
    overlap_cameras = _rank(cameras, [c for c in cameras if cameras[c]['role'] in ['wide', 'two-shot']])
    segments, current, shot_start = [], None, start
    reason_codes = ['START_CAMERA']
    previous_override = None
    fallback_requested = None
    frame = start
    while frame < end:
        atom = atoms[bisect_right(atom_starts, frame)-1]
        speakers = sorted(atom['speakers'])
        override = next((item for item in overrides if item[0] <= frame < item[1]), None)
        override_ended = previous_override is not None and override is None
        desired, cause, exceptional = current, 'HOLD', None
        overlap_unavailable = False
        if override:
            desired, cause, exceptional = override[2], 'MANUAL_OVERRIDE', 'MIN_SHOT_EXCEPTION_OVERRIDE'
        elif atom['unknown']:
            cause = 'UNKNOWN_HOLD'
        elif atom['sustained']:
            suitable = [c for c in overlap_cameras if set(speakers).issubset(cameras[c]['coveredSpeakers']) and _at(bindings, c, frame)]
            if suitable:
                desired, cause, exceptional = suitable[0], 'OVERLAP_SUSTAINED', 'MIN_SHOT_EXCEPTION_OVERLAP'
            else:
                cause = 'OVERLAP_HOLD'
                overlap_unavailable = True
        elif len(speakers) == 1:
            speaker = speakers[0]
            if frame == start or not suppress or atom['speakers'][speaker] > short:
                desired = mapping['speakers'][speaker]
                cause = 'START_SPEAKER' if frame == start else 'SPEAKER_TURN'
        if current is None and desired is None:
            desired, cause = mapping['startCameraId'], 'START_CAMERA'
        if override_ended and desired != current:
            exceptional = 'MIN_SHOT_EXCEPTION_OVERRIDE_END'
            cause = 'OVERRIDE_END'
        current_available = _at(bindings, current, frame) is not None
        requested = desired
        # A desired-camera gap never breaks a valid current camera's minimum.
        if current is not None and desired != current and current_available and not exceptional and frame < shot_start + minimum:
            desired, cause = current, 'MIN_SHOT_HOLD'
        desired_missing = _at(bindings, desired, frame) is None
        fallback = False
        if desired_missing:
            if override:
                _fail('OVERRIDE_COVERAGE_GAP', 'Fixed camera became unavailable', startFrame=frame, endFrame=frame+1, cameraId=desired)
            candidates = wide + ([current] if current is not None else []) + mapping.get('fallbackOrder', [])
            desired = next((c for c in candidates if _at(bindings, c, frame)), None)
            if desired is None:
                next_event = events[bisect_right(events, frame)]
                _fail('VIDEO_COVERAGE_GAP', 'No permitted camera covers this interval', startFrame=frame, endFrame=next_event, cameraIds=sorted(cameras))
            fallback, cause = True, 'VIDEO_GAP_FALLBACK'
        if current is not None and not current_available and desired != current:
            exceptional = 'MIN_SHOT_EXCEPTION_COVERAGE'
        changed = current != desired
        # Preserve a live coverage-fallback cause across unknown/short-turn and
        # minimum-hold decisions. A hold changes eligibility, not the reason this
        # camera was selected. Recovery or an intentional new camera ends it.
        if override or (changed and not fallback):
            fallback_requested = None
        elif fallback:
            fallback_requested = requested
        elif fallback_requested is not None and _at(bindings, fallback_requested, frame):
            fallback_requested = None
        if changed:
            reason_codes = [cause]
            if current is not None and exceptional and frame < shot_start + minimum:
                reason_codes.append(exceptional)
            shot_start = frame
            current = desired
        next_frame = events[bisect_right(events, frame)]
        if frame < shot_start + minimum < next_frame:
            next_frame = shot_start + minimum
        if atom['unknown']:
            reviews.append(dict(code='UNKNOWN_SPEAKER', startFrame=frame, endFrame=next_frame, reasons=atom['unknown']))
        if overlap_unavailable:
            reviews.append(dict(code='OVERLAP_CAMERA_UNAVAILABLE', startFrame=frame, endFrame=next_frame, speakers=speakers))
        if fallback_requested is not None:
            reviews.append(dict(code='VIDEO_GAP_FALLBACK', startFrame=frame, endFrame=next_frame, requestedCameraId=fallback_requested, cameraId=current))
        key = _at(bindings, current, frame)
        segment = _segment(frame, next_frame, current, key, reason_codes, snapshot, clips)
        if segments and segments[-1]['cameraId'] == current and segments[-1]['sourceClipInstanceKey'] == key and segments[-1]['sourceOut'] == segment['sourceIn']:
            segments[-1]['endFrame'] = next_frame
            segments[-1]['sourceOut'] = segment['sourceOut']
        else:
            segments.append(segment)
        previous_override = override
        frame = next_frame
    if end - shot_start < minimum:
        segments[-1]['reasonCodes'].append('RANGE_END_SHORT')
    plan = dict(schemaVersion=SCHEMA_VERSION, snapshotHash=snapshot['snapshotHash'], segments=segments, reviews=_reviews(reviews))
    plan['planHash'] = canonical_hash(plan)
    return validate_plan(plan, snapshot, mapping)


def plan_edit(snapshot, analysis, mapping, policy):
    """Return a complete covered plan or raise CutError; inputs are never mutated."""
    try:
        return _plan_edit(snapshot, analysis, mapping, policy)
    except CutError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
        raise CutError('INVALID_CONTRACT', 'Malformed planning input', {'cause': str(error)}) from error


def _validate_plan(plan, snapshot, mapping):
    start, end, tracks, sources, clips = _snapshot(snapshot)
    if integer(plan['schemaVersion'], 'schemaVersion') != SCHEMA_VERSION:
        _fail('INVALID_PLAN', 'Unsupported plan schema')
    if plan['snapshotHash'] != snapshot['snapshotHash']:
        _fail('SNAPSHOT_MISMATCH', 'Plan belongs to another input snapshot')
    if plan['planHash'] != canonical_hash({key: value for key, value in plan.items() if key != 'planHash'}):
        _fail('PLAN_HASH_MISMATCH', 'Plan content changed after validation')
    cameras, bindings = _mapping(mapping, snapshot, tracks, sources, clips) if mapping is not None else (None, None)
    cursor = start
    for segment in plan['segments']:
        first, last = _range(segment, 'INVALID_PLAN')
        if not isinstance(segment.get('reason'), str) or not segment['reason']:
            _fail('INVALID_PLAN', 'Segment must explain its selection')
        camera_id, key = segment['cameraId'], segment['sourceClipInstanceKey']
        if first != cursor or last > end:
            _fail('INVALID_PLAN', 'Plan has a gap, overlap or out-of-range boundary', expectedStartFrame=cursor, startFrame=first, endFrame=last)
        if not isinstance(camera_id, str) or not camera_id or key not in clips:
            _fail('INVALID_PLAN', 'Plan must use registered video sources')
        clip = clips[key]
        clip_start, clip_end, source_in, source_out, available = _source(clip, tracks, sources)
        if tracks[clip['trackRef']].get('protected', False):
            _fail('INVALID_PLAN', 'Plan cannot use protected tracks', instanceKey=key)
        first_tick, last_tick = tick_int(frame_ticks(first, snapshot['fps'])), tick_int(frame_ticks(last, snapshot['fps']))
        expected_in, expected_out = source_in+first_tick-clip_start, source_in+last_tick-clip_start
        if not available or first_tick < clip_start or last_tick > clip_end or expected_in < source_in or expected_out > source_out:
            _fail('INVALID_PLAN', 'Segment cannot read complete source frames', instanceKey=key)
        if tick_int(segment['sourceIn']) != expected_in or tick_int(segment['sourceOut']) != expected_out:
            _fail('INVALID_PLAN', 'Segment source ticks do not match the sequence mapping', instanceKey=key)
        if cameras is not None:
            if camera_id not in cameras or _missing([(a, b) for a, b, source in bindings[camera_id] if source == key], first, last):
                _fail('INVALID_PLAN', 'Segment source does not belong to the camera coverage', cameraId=camera_id, instanceKey=key)
        cursor = last
    if cursor != end:
        _fail('INVALID_PLAN', 'Plan does not cover the entire target range', startFrame=cursor, endFrame=end)
    return plan


def validate_plan(plan, snapshot, mapping=None):
    """Validate plan integrity, contiguous range and exact registered source ticks."""
    try:
        return _validate_plan(plan, snapshot, mapping)
    except CutError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
        raise CutError('INVALID_PLAN', 'Malformed edit plan', {'cause': str(error)}) from error
