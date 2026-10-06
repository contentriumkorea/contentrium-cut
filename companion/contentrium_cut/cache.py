"""Completed result envelopes. Corrupt/legacy entries are ordinary cache misses."""
import json
import hashlib
import math
import re
from pathlib import Path

from .contract import CutError

ENGINE_SCHEMA = 3


def result_digest(result):
    # Cache output is local Python JSON, including exact nanosecond stat integers.
    # RFC 8785's JS-safe integer limit is appropriate for API plan hashes, but not
    # for this persisted decoder metadata. ENGINE_SCHEMA pins this encoding.
    encoded = json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _require(condition):
    if not condition: raise ValueError('Invalid result cache')


def _integer(value):
    return type(value) is int


def _strings(values):
    return (isinstance(values, list) and all(isinstance(s, str) and s for s in values)
            and len(set(values)) == len(values))


def _numbers_and_ranges(value):
    if isinstance(value, float): _require(math.isfinite(value))
    elif isinstance(value, dict):
        _require(all(isinstance(key, str) for key in value))
        for start, end in [('startFrame', 'endFrame'), ('startSample', 'endSample')]:
            if start in value or end in value:
                _require(start in value and end in value and _integer(value[start]) and _integer(value[end]))
                _require(value[end] > value[start])
                if start == 'startSample': _require(value[start] >= 0)
        for item in value.values(): _numbers_and_ranges(item)
    elif isinstance(value, list):
        for item in value: _numbers_and_ranges(item)


def validate_result(kind, result, payload=None):
    try:
        _require(isinstance(result, dict) and type(result.get('schemaVersion')) is int and result['schemaVersion'] == 1)
        _numbers_and_ranges(result)
        _require(isinstance(result.get('reviews'), list) and all(isinstance(r, dict) and isinstance(r.get('code'), str) for r in result['reviews']))
        if kind == 'analysis':
            _require(isinstance(result.get('modelRevision'), str) and result['modelRevision'])
            _require(_strings(result.get('sessionSpeakerIds')))
            _require(isinstance(result.get('intervals'), list) and isinstance(result.get('validAudioRanges'), list))
            _require(isinstance(result.get('evidence'), (dict, list)))
            for row in result['intervals']:
                _require(isinstance(row, dict) and 'startFrame' in row and 'endFrame' in row)
                _require(row['startFrame'] >= 0 and _strings(row.get('speakers')) and type(row.get('unknown')) is bool)
                _require(set(row['speakers']) <= set(result['sessionSpeakerIds']))
            previous = None
            for row in result['intervals']:
                _require(previous is None or row['startFrame'] >= previous)
                previous = row['endFrame']
            for row in result['validAudioRanges']:
                _require(isinstance(row, dict) and isinstance(row.get('assetId'), str) and row['assetId'])
                _require(all(k in row for k in ('startFrame', 'endFrame', 'startSample', 'endSample')))
                _require(_integer(row.get('sampleRate')) and row['sampleRate'] > 0)
            coverage = []
            for row in sorted(result['validAudioRanges'], key=lambda r: r['startFrame']):
                if coverage and row['startFrame'] <= coverage[-1][1]:
                    coverage[-1][1] = max(coverage[-1][1], row['endFrame'])
                else: coverage.append([row['startFrame'], row['endFrame']])
            _require(all(any(lo <= row['startFrame'] and row['endFrame'] <= hi for lo, hi in coverage)
                         for row in result['intervals']))
            if payload is not None:
                settings = payload['settings']; frame_range = settings.get('range')
                if settings.get('modelRevision'): _require(result['modelRevision'] == settings['modelRevision'])
                if frame_range:
                    _require(all(frame_range['startFrame'] <= r['startFrame'] < r['endFrame'] <= frame_range['endFrame'] for r in result['intervals']))
                assets = {source['assetId'] for source in payload.get('sources', [])}
                _require(all(r['assetId'] in assets for r in result['validAudioRanges']))
        elif kind == 'sync':
            reference = result.get('referenceAssetId'); sources = result.get('sources'); offsets = result.get('offsets')
            _require(isinstance(reference, str) and isinstance(sources, dict) and isinstance(offsets, dict) and reference in sources)
            _require(sources[reference].get('status') == 'accepted' and type(offsets.get(reference)) in (int, float) and offsets[reference] == 0)
            _require(isinstance(result.get('edges'), list))
            for asset, row in sources.items():
                _require(isinstance(asset, str) and isinstance(row, dict) and row.get('status') in ('accepted', 'review', 'unresolved'))
                _require(_strings(row.get('path')) and isinstance(row.get('validSourceRange'), dict))
                valid = row['validSourceRange']
                _require(all(k in valid for k in ('startSample', 'endSample')) and _integer(valid.get('sampleRate')) and valid['sampleRate'] > 0)
                if row['status'] == 'accepted':
                    _require(asset in offsets and row['path'] and row['path'][0] == reference and row['path'][-1] == asset)
                    _require(all(p in sources and sources[p]['status'] == 'accepted' for p in row['path']))
                else: _require(asset not in offsets)
            for asset, offset in offsets.items():
                _require(asset in sources and sources[asset]['status'] == 'accepted' and type(offset) in (int, float) and math.isfinite(offset))
            for edge in result['edges']:
                _require(isinstance(edge, dict) and edge.get('fromAssetId') in sources and edge.get('toAssetId') in sources)
                _require(edge.get('status') in ('accepted', 'review', 'drift'))
            if payload is not None:
                _require(reference == payload['reference'] and set(sources) == {s['assetId'] for s in payload['sources']})
        else: _require(False)
        result_digest(result)  # Reject unsupported JSON/number values too.
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError, RecursionError):
        raise CutError('INVALID_WORKER_RESULT', 'Local analysis produced an invalid result.') from None


def envelope(kind, key, result):
    if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{64}', key):
        raise CutError('INVALID_WORKER_RESULT', 'Invalid result cache identity.')
    validate_result(kind, result)
    return {'schemaVersion': 1, 'engineSchema': ENGINE_SCHEMA, 'status': 'completed',
            'kind': kind, 'key': key, 'modelRevision': result.get('modelRevision'),
            'resultDigest': result_digest(result), 'result': result}


def load(path, kind, key, payload):
    try:
        def unique_object(pairs):
            value = {}
            for name, item in pairs:
                _require(name not in value)
                value[name] = item
            return value
        value = json.loads(Path(path).read_text(encoding='utf-8'),
                           object_pairs_hook=unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
        _require(isinstance(value, dict) and set(value) == {
            'schemaVersion', 'engineSchema', 'status', 'kind', 'key', 'modelRevision', 'resultDigest', 'result'})
        _require(type(value['schemaVersion']) is int and value['schemaVersion'] == 1)
        _require(type(value['engineSchema']) is int and value['engineSchema'] == ENGINE_SCHEMA)
        _require(value['status'] == 'completed' and value['kind'] == kind and value['key'] == key)
        result = value['result']
        validate_result(kind, result, payload)
        _require(value['modelRevision'] == result.get('modelRevision') and value['resultDigest'] == result_digest(result))
        return result
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, CutError):
        return None
