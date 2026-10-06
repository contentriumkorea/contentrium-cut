import copy
import json
import queue
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from contentrium_cut.contract import canonical_hash
from contentrium_cut.jobs import _worker
from contentrium_cut.cache import envelope, load, result_digest


def analysis_fixture():
    return {'schemaVersion': 1, 'modelRevision': 'model-1',
            'intervals': [{'startFrame': 0, 'endFrame': 30, 'speakers': ['A'], 'unknown': False}],
            'sessionSpeakerIds': ['A'], 'reviews': [],
            'validAudioRanges': [{'assetId': 'a', 'startFrame': 0, 'endFrame': 30,
                                 'startSample': 0, 'endSample': 16000, 'sampleRate': 16000}],
            'evidence': []}


def analysis_payload():
    return {'mode': 'separate', 'sources': [], 'settings': {
        'fps': {'num': 30, 'den': 1}, 'range': {'startFrame': 0, 'endFrame': 30},
        'modelRevision': 'model-1'}}


def sync_fixture():
    return {'schemaVersion': 1, 'referenceAssetId': 'a', 'offsets': {'a': 0.0, 'b': 2.0},
            'sources': {asset: {'status': 'accepted', 'path': ['a'] if asset == 'a' else ['a', 'b'],
                               'pcmOriginSeconds': 0.0, 'size': 32000, 'mtimeNs': 123,
                               'validSourceRange': {'startSample': 0, 'endSample': 16000, 'sampleRate': 16000}}
                        for asset in ['a', 'b']}, 'edges': [], 'reviews': []}


class CacheTests(unittest.TestCase):
    def load_fixture(self, value, payload=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ('a' * 64 + '.json')
            path.write_text(json.dumps(value))
            return load(path, 'analysis', 'a' * 64, payload or {
                **analysis_payload(), 'sources': [{'assetId': 'a'}]})

    def test_valid_complete_envelope_is_reusable(self):
        result = self.load_fixture(envelope('analysis', 'a' * 64, analysis_fixture()))
        self.assertEqual(result['intervals'][0]['speakers'], ['A'])

    def test_modified_result_and_wrong_envelope_identities_are_misses(self):
        good = envelope('analysis', 'a' * 64, analysis_fixture())
        cases = []
        for key, value in [('schemaVersion', True), ('engineSchema', 1), ('status', 'pending'),
                           ('kind', 'sync'), ('key', 'b' * 64), ('modelRevision', 'other'),
                           ('resultDigest', '0' * 64)]:
            changed = copy.deepcopy(good); changed[key] = value; cases.append(changed)
        changed = copy.deepcopy(good); changed['result']['intervals'][0]['unknown'] = True; cases.append(changed)
        for value in cases:
            with self.subTest(value=value): self.assertIsNone(self.load_fixture(value))

    def test_valid_digest_does_not_admit_malformed_ranges_or_unknown_identity(self):
        cases = []
        for key, value in [('startFrame', -1), ('startFrame', True), ('endFrame', 0),
                           ('endFrame', 31), ('endFrame', 1.5), ('unknown', 1),
                           ('speakers', ['OTHER']), ('speakers', ['A', 'A'])]:
            changed = analysis_fixture(); changed['intervals'][0][key] = value; cases.append(changed)
        for key, value in [('startSample', -1), ('endSample', 0), ('sampleRate', True),
                           ('assetId', 'outside'), ('startFrame', 1), ('endFrame', 29)]:
            changed = analysis_fixture(); changed['validAudioRanges'][0][key] = value; cases.append(changed)
        changed = analysis_fixture(); changed['intervals'].append(copy.deepcopy(changed['intervals'][0])); cases.append(changed)
        changed = analysis_fixture(); changed['modelRevision'] = 'wrong'; cases.append(changed)
        changed = analysis_fixture(); changed['validAudioRanges'] = []; cases.append(changed)
        for result in cases:
            value = {'schemaVersion': 1, 'engineSchema': 2, 'status': 'completed', 'kind': 'analysis',
                     'key': 'a' * 64, 'modelRevision': result['modelRevision'],
                     'resultDigest': result_digest(result), 'result': result}
            with self.subTest(result=result): self.assertIsNone(self.load_fixture(value))

    def test_nonfinite_and_incomplete_json_are_misses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.json'
            for text in ['{"result":NaN}', '{', 'null', '[]']:
                path.write_text(text); self.assertIsNone(load(path, 'analysis', 'a' * 64, analysis_payload()))

    def test_duplicate_json_identity_fields_are_not_admitted(self):
        good = json.dumps(envelope('analysis', 'a' * 64, analysis_fixture()))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.json'
            path.write_text(good.replace('"status": "completed"', '"status":"pending","status":"completed"'))
            self.assertIsNone(load(path, 'analysis', 'a' * 64, {**analysis_payload(), 'sources': [{'assetId': 'a'}]}))

    def test_sync_reference_paths_offsets_and_source_ranges_are_validated(self):
        payload = {'reference': 'a', 'sources': [{'assetId': 'a'}, {'assetId': 'b'}]}
        good = sync_fixture(); cases = []
        for offset in [True, '2.0']:
            value = sync_fixture(); value['offsets']['b'] = offset; cases.append(value)
        for field, replacement in [('status', 'review'), ('path', ['a', 'outside']),
                                   ('path', ['b']), ('path', ['a', 'a', 'b']),
                                   ('validSourceRange', {'startSample': 2, 'endSample': 1, 'sampleRate': 16000})]:
            value = sync_fixture(); value['sources']['b'][field] = replacement; cases.append(value)
        value = sync_fixture(); value['offsets']['a'] = 1; cases.append(value)
        value = sync_fixture(); value['edges'] = [{'fromAssetId': 'a', 'toAssetId': 'outside'}]; cases.append(value)
        value = sync_fixture(); value['edges'] = [{'fromAssetId': 'a', 'toAssetId': 'b', 'status': 'made-up'}]; cases.append(value)
        value = sync_fixture(); value['sources']['b']['validSourceRange']['sampleRate'] = True; cases.append(value)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.json'
            path.write_text(json.dumps(envelope('sync', 'a' * 64, good)))
            self.assertEqual(load(path, 'sync', 'a' * 64, payload)['offsets'], {'a': 0.0, 'b': 2.0})
            for result in cases:
                value = {'schemaVersion': 1, 'engineSchema': 2, 'status': 'completed', 'kind': 'sync',
                         'key': 'a' * 64, 'modelRevision': None, 'resultDigest': result_digest(result), 'result': result}
                path.write_text(json.dumps(value))
                with self.subTest(result=result): self.assertIsNone(load(path, 'sync', 'a' * 64, payload))

    def test_unresolved_sync_sources_remain_valid_without_usable_offsets(self):
        result = sync_fixture(); result['sources']['b'].update(status='unresolved', path=[]); result['offsets'].pop('b')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.json'; path.write_text(json.dumps(envelope('sync', 'a' * 64, result)))
            value = load(path, 'sync', 'a' * 64, {'reference': 'a', 'sources': [{'assetId': 'a'}, {'assetId': 'b'}]})
            self.assertEqual(value['sources']['b']['status'], 'unresolved'); self.assertNotIn('b', value['offsets'])

    def test_actual_ffmpeg_sync_nanosecond_metadata_roundtrips_completed_cache(self):
        import numpy as np
        from test_audio import FFMPEG
        from contentrium_cut.jobs import JobManager
        with tempfile.TemporaryDirectory() as directory:
            pcm = (np.random.default_rng(19).normal(0, .15, 64000) * 30000).astype('<i2')
            sources = []
            for asset in ['a', 'b']:
                source = Path(directory) / (asset + '.wav')
                with wave.open(str(source), 'wb') as stream:
                    stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000); stream.writeframes(pcm.tobytes())
                sources.append({'assetId': asset, 'path': str(source), 'ffmpeg': FFMPEG})
            payload = {'reference': 'a', 'sources': sources, 'fps': {'num': 30, 'den': 1}}
            output = queue.Queue(); jobs = JobManager(Path(directory) / 'jobs-root')
            try:
                _worker('sync', payload, threading.Event(), output, str(jobs.root / 'cache'))
                first = output.get_nowait(); self.assertTrue(first['ok'], first)
                self.assertGreater(first['value']['sources']['a']['mtimeNs'], 2**53)
                jobs.jobs['sync'] = {'jobId': 'sync', 'kind': 'sync', 'status': 'running', 'epoch': 0}
                self.assertTrue(jobs.commit_result('sync', 0, first['value'], first['cacheKey']))
                with patch('contentrium_cut.audio.sync_sources', side_effect=AssertionError('valid cache missed')):
                    _worker('sync', payload, threading.Event(), output, str(jobs.root / 'cache'))
                cached = output.get_nowait(); self.assertTrue(cached['ok'], cached)
                self.assertEqual(cached['value']['sources']['a']['mtimeNs'], first['value']['sources']['a']['mtimeNs'])
                self.assertEqual(cached['value']['offsets'], {'a': 0.0, 'b': 0.0})
            finally: jobs.close()

    def test_validated_cache_is_returned_without_another_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.wav'; source.write_bytes(b'unchanged fixture media')
            payload = analysis_payload(); payload['sources'] = [{'assetId': 'a', 'path': str(source)}]
            output = queue.Queue()
            with patch('contentrium_cut.audio.analyze_audio', return_value=analysis_fixture()):
                _worker('analysis', payload, threading.Event(), output, directory)
            first = output.get_nowait(); self.assertTrue(first['ok'])
            Path(directory, first['cacheKey'] + '.json').write_text(json.dumps(envelope('analysis', first['cacheKey'], first['value'])))
            with patch('contentrium_cut.audio.analyze_audio', side_effect=AssertionError('cache missed')):
                _worker('analysis', payload, threading.Event(), output, directory)
            hit = output.get_nowait(); self.assertTrue(hit['ok']); self.assertEqual(hit['value']['sessionSpeakerIds'], ['A'])

    def test_source_changed_during_cache_read_cannot_complete(self):
        from contentrium_cut.cache import load as original_load
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.wav'; source.write_bytes(b'original source')
            payload = analysis_payload(); payload['sources'] = [{'assetId': 'a', 'path': str(source)}]
            output = queue.Queue()
            with patch('contentrium_cut.audio.analyze_audio', return_value=analysis_fixture()):
                _worker('analysis', payload, threading.Event(), output, directory)
            first = output.get_nowait()
            Path(directory, first['cacheKey'] + '.json').write_text(json.dumps(envelope('analysis', first['cacheKey'], first['value'])))
            def changing_load(*args):
                value = original_load(*args); source.write_bytes(b'changed source during cache read'); return value
            with patch('contentrium_cut.jobs.load_cache', side_effect=changing_load):
                _worker('analysis', payload, threading.Event(), output, directory)
            outcome = output.get_nowait(); self.assertFalse(outcome['ok']); self.assertEqual(outcome['error']['code'], 'SOURCE_CHANGED')

    def test_legacy_valid_json_is_recomputed_instead_of_trusted(self):
        payload = analysis_payload()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.wav'; source.write_bytes(b'unchanged fixture media')
            payload['sources'] = [{'assetId': 'a', 'path': str(source)}]
            from contentrium_cut.jobs import _fingerprints
            # Legacy caches were raw output JSON without an integrity envelope.
            key = canonical_hash({'kind': 'analysis', 'payload': payload, 'files': _fingerprints(payload['sources'], threading.Event()), 'engineSchema': 1})
            old = analysis_fixture(); old['intervals'][0]['speakers'] = ['WRONG']
            Path(directory, key + '.json').write_text(json.dumps(old))
            output = queue.Queue()
            with patch('contentrium_cut.audio.analyze_audio', return_value=analysis_fixture()):
                _worker('analysis', payload, threading.Event(), output, directory)
            self.assertEqual(output.get_nowait()['value']['intervals'][0]['speakers'], ['A'])

    def test_fresh_malformed_analysis_is_failed_not_completed(self):
        malformed = analysis_fixture(); malformed['intervals'][0]['endFrame'] = -1
        with tempfile.TemporaryDirectory() as directory:
            output = queue.Queue()
            with patch('contentrium_cut.audio.analyze_audio', return_value=malformed):
                _worker('analysis', analysis_payload(), threading.Event(), output, directory)
            self.assertFalse(output.get_nowait()['ok'])
