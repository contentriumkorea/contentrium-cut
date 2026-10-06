"""Recording instances and people have independent identities."""
import math
from .contract import CutError


def source_key(source):
    key = source.get('inputKey') or source.get('instanceKey') or source.get('assetId')
    if not isinstance(key, str) or not key.strip():
        raise CutError('INVALID_AUDIO_INPUT', 'Recording input identity is missing.')
    return key


def channel_index(value):
    if type(value) is not int or value < 0:
        raise CutError('INVALID_AUDIO_INPUT', 'Stream and channel indices must be nonnegative integers.')
    return value


def check_assignments(channels, sources, decoded=None):
    """Allow repeated/disjoint recordings, block contradictory overlapping labels."""
    rows = []; seen = set()
    for index, (channel, source) in enumerate(zip(channels, sources)):
        sid = channel.get('speakerId')
        if not isinstance(sid, str) or not sid.strip():
            raise CutError('INVALID_AUDIO_INPUT', 'Confirm a session speaker for each microphone.')
        stream = channel_index(channel.get('streamIndex', source.get('streamIndex', 0)))
        selected = channel_index(channel.get('channelIndex', source.get('channelIndex', 0)))
        identity = (source_key(source), stream, selected)
        if identity in seen: raise CutError('INVALID_AUDIO_INPUT', 'A recording channel was selected twice.')
        seen.add(identity)
        if decoded is not None:
            info = decoded[index]; start = info['sessionOrigin']; duration = len(info['samples']) / 16000
            source_start = info['origin']
        elif all(k in source for k in ('durationSeconds', 'sequenceStartSeconds')):
            start = source['sequenceStartSeconds']; duration = source['durationSeconds']; source_start = source.get('sourceStartSeconds', 0)
        else: continue  # Media probing will establish exact bounds before inference.
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (start, duration, source_start)) or duration <= 0:
            raise CutError('INVALID_AUDIO_INPUT', 'Recording bounds must be finite and have positive duration.')
        physical = (source['assetId'], stream, selected)
        for other_sid, other_physical, other_start, other_end, other_in, other_out in rows:
            raw_overlap = source_start < other_out and other_in < source_start + duration
            placed_overlap = start < other_end and other_start < start + duration
            if physical == other_physical and sid != other_sid and raw_overlap:
                raise CutError('SOURCE_ASSIGNMENT_CONFLICT', 'The same source samples cannot belong to different people.')
            if sid == other_sid and placed_overlap:
                aligned = physical == other_physical and abs((start - source_start) - (other_start - other_in)) < 1 / 16000
                if not aligned:
                    raise CutError('SOURCE_ASSIGNMENT_CONFLICT', 'Overlapping recordings of one person need an unambiguous segment assignment.')
        rows.append((sid, physical, start, start + duration, source_start, source_start + duration))
