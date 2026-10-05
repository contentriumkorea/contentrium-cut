import importlib
import unittest


class ContractTests(unittest.TestCase):
    def setUp(self):
        try:
            self.contract = importlib.import_module('contentrium_cut.contract')
        except ModuleNotFoundError:
            self.fail('Time contract implementation is missing')

    def test_ntsc_one_hour_has_no_cumulative_tick_drift(self):
        self.assertEqual(self.contract.frame_ticks(108000, {'num': 30000, 'den': 1001}), '915372057600000')
        self.assertEqual(self.contract.ticks_frame('915372057600000', {'num': 30000, 'den': 1001}), 108000)

    def test_shared_half_frame_boundary_rounds_forward(self):
        self.assertEqual(self.contract.ticks_frame('88905600000', {'num': 30, 'den': 1}), 11)
        self.assertEqual(self.contract.ticks_frame('-4233600000', {'num': 30, 'den': 1}), 0)

    def test_frame_ticks_preserves_negative_sequence_coordinate(self):
        self.assertEqual(self.contract.frame_ticks(-1, {'num': 30, 'den': 1}), '-8467200000')

    def test_fractional_tick_frame_mapping_is_blocked(self):
        with self.assertRaises(self.contract.CutError) as result:
            self.contract.frame_ticks(1, {'num': 11, 'den': 1})
        self.assertEqual(result.exception.code, 'TIME_MAPPING_UNSUPPORTED')

    def test_invalid_time_inputs_are_typed_errors(self):
        for frame, fps in [(True, {'num': 30, 'den': 1}), (1.5, {'num': 30, 'den': 1}), (1, {'num': 0, 'den': 1}), (1, {'num': 30, 'den': 0})]:
            with self.subTest(frame=frame, fps=fps), self.assertRaises(self.contract.CutError):
                self.contract.frame_ticks(frame, fps)
        for ticks in ['1.5', 'NaN', True, 1.2]:
            with self.subTest(ticks=ticks), self.assertRaises(self.contract.CutError):
                self.contract.ticks_frame(ticks, {'num': 30, 'den': 1})

    def test_canonical_hash_ignores_key_order_but_not_values(self):
        self.assertEqual(self.contract.canonical_hash({'b': 2, 'a': 1}), '43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777')
        self.assertEqual(self.contract.canonical_hash({'b': 2, 'a': 1}), self.contract.canonical_hash({'a': 1, 'b': 2}))
        self.assertNotEqual(self.contract.canonical_hash({'a': 1}), self.contract.canonical_hash({'a': 1, 'planHash': 'x'}))

    def test_actual_javascript_float_and_unicode_hashes_match(self):
        import json,shutil,subprocess
        node=shutil.which('node')
        if not node:self.skipTest('Node required for cross-language hash verification')
        value={'reviews':[{'calibrationPatterns':{'A':{'A':1.0,'B':1e-6}},'correlations':[.9999999,1.0], 'zero':-0.0}], '\U0001f600':1,'\ufffd':2,'small':1e-7,'large':1e21}
        script="const crypto=require('node:crypto');const fs=require('node:fs');function stable(v){if(Array.isArray(v))return '['+v.map(stable).join(',')+']';if(v&&typeof v==='object')return '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}';return JSON.stringify(v);}const v=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(crypto.createHash('sha256').update(stable(v)).digest('hex'));"
        result=subprocess.run([node,'-e',script],input=json.dumps(value).encode(),capture_output=True,check=True)
        self.assertEqual(self.contract.canonical_hash(value),result.stdout.decode())

    def test_nonfinite_or_non_json_hash_values_fail_closed(self):
        for value in [float('nan'), float('inf'), {'a': {1, 2}}]:
            with self.subTest(value=value), self.assertRaises(self.contract.CutError) as result:
                self.contract.canonical_hash(value)
            self.assertEqual(result.exception.code, 'INVALID_CONTRACT')

    def test_typed_error_carries_details_without_flattening(self):
        error = self.contract.CutError('MISSING_AUDIO', 'Missing range', {'startFrame': 100, 'endFrame': 101})
        self.assertEqual(error.code, 'MISSING_AUDIO')
        self.assertEqual(error.message, 'Missing range')
        self.assertEqual(error.details, {'startFrame': 100, 'endFrame': 101})

    def test_canonical_hash_rejects_non_string_keys_instead_of_aliasing_them(self):
        with self.assertRaises(self.contract.CutError):
            self.contract.canonical_hash({1: 'camera'})
        with self.assertRaises(self.contract.CutError):
            self.contract.canonical_hash({'nested': {False: 'camera'}})


if __name__ == '__main__':
    unittest.main()
