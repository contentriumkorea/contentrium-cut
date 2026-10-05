import copy
import importlib
import unittest


def fixture(end=200, coverage=None):
    coverage = coverage or {'CA': [(0, end)], 'CB': [(0, end)], 'W': [(0, end)]}
    cameras, clips, sources, tracks = [], [], [], []
    for number, (camera_id, ranges) in enumerate(coverage.items()):
        bindings = []
        track = camera_id + '-track'
        tracks.append(dict(trackRef=track, mediaType='video', index=number, name=camera_id, muted=False, protected=False))
        for index, (start, stop) in enumerate(ranges):
            key = f'{camera_id}-{index}'
            sources.append(dict(assetId=key, canonicalPath='C:/registered/' + key + '.mp4'))
            clips.append(dict(instanceKey=key, assetId=key, projectItemRef=key, trackRef=track, mediaType='video', startTicks=str(start * 8467200000), endTicks=str(stop * 8467200000), inTicks='0', outTicks=str((stop-start)*8467200000), speed=1, disabled=False, channelMap=None, effectFingerprint='none', supportFlags=dict(timeMappingSupported=True, effectPreservationSupported=True, linkedAudioSafe=True)))
            bindings.append(dict(instanceKey=key, startFrame=start, endFrame=stop))
        cameras.append(dict(cameraId=camera_id, role='wide' if camera_id == 'W' else 'speaker', coveredSpeakers=['A', 'B'] if camera_id == 'W' else [camera_id[-1]], clips=bindings))
    snapshot = dict(schemaVersion=1, projectRef='project', sequenceRef='sequence', projectName='Interview', sequenceName='Input', fps=dict(num=30, den=1), range=dict(startFrame=0, endFrame=end), tracks=tracks, clips=clips, sources=sources, supportFlags=dict(timeMappingSupported=True, audioPreservationSupported=True, effectPreservationSupported=True), snapshotHash='fixture-snapshot')
    analysis = dict(schemaVersion=1, modelRevision='verified-local', intervals=[], sessionSpeakerIds=['A', 'B'], reviews=[], validAudioRanges=[dict(startFrame=0, endFrame=end)])
    mapping = dict(speakers=dict(A='CA', B='CB'), cameras=cameras, startCameraId='W', fallbackOrder=[])
    policy = dict(minShot=2.0, shortTurn=0.6, suppressShort=True, overlap=0.8, overrides=[])
    return snapshot, analysis, mapping, policy


def speech(start, end, *speakers, unknown=False):
    return dict(startFrame=start, endFrame=end, speakers=list(speakers), unknown=unknown)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module('contentrium_cut.policy')
            self.error = importlib.import_module('contentrium_cut.contract').CutError
        except ModuleNotFoundError:
            self.fail('Deterministic policy implementation is missing')

    def plan(self, args):
        return self.module.plan_edit(*args)

    def shots(self, plan):
        return [(item['startFrame'], item['endFrame'], item['cameraId']) for item in plan['segments']]

    def blocked(self, args, code):
        with self.assertRaises(self.error) as result:
            self.plan(args)
        self.assertEqual(result.exception.code, code)
        return result.exception.details

    def test_approved_600_frame_example_exactly(self):
        args = fixture(600, {'CA': [(0, 360), (420, 600)], 'CB': [(0, 600)], 'W': [(0, 600)]})
        args[1]['intervals'] = [speech(0, 150, 'A'), speech(240, 276, 'A'), speech(360, 480, 'A'), speech(519, 600, 'A'), speech(72, 84, 'B'), speech(150, 264, 'B'), speech(300, 360, 'B'), speech(495, 519, 'B'), speech(480, 495, unknown=True)]
        before = copy.deepcopy(args)
        plan = self.plan(args)
        self.assertEqual(self.shots(plan), [(0, 150, 'CA'), (150, 240, 'CB'), (240, 300, 'W'), (300, 360, 'CB'), (360, 420, 'W'), (420, 495, 'CA'), (495, 555, 'CB'), (555, 600, 'CA')])
        self.assertEqual(args, before)
        self.assertEqual(sum(x['endFrame'] - x['startFrame'] for x in plan['segments']), 600)
        self.assertTrue(any(r['code'] == 'VIDEO_GAP_FALLBACK' for r in plan['reviews']))
        self.assertTrue(any(r['code'] == 'UNKNOWN_SPEAKER' and r['startFrame'] == 480 and r['endFrame'] == 495 for r in plan['reviews']))
        self.assertEqual([self.plan(args) for _ in range(3)], [plan, plan, plan])

    def test_short_threshold_is_inclusive_and_off_disables_only_suppression(self):
        for duration, suppress, expected in [(18, True, [(0, 200, 'CA')]), (19, True, [(0, 90, 'CA'), (90, 200, 'CB')]), (18, False, [(0, 90, 'CA'), (90, 200, 'CB')])]:
            args = fixture()
            args[1]['intervals'] = [speech(0, 90, 'A'), speech(90, 90+duration, 'B')]
            args[3]['suppressShort'] = suppress
            with self.subTest(duration=duration, suppress=suppress):
                self.assertEqual(self.shots(self.plan(args)), expected)

    def test_delayed_turn_is_rechecked_and_expired_turn_is_discarded(self):
        for end, expected in [(100, [(0, 60, 'CA'), (60, 200, 'CB')]), (59, [(0, 200, 'CA')]), (60, [(0, 200, 'CA')])]:
            args = fixture()
            args[1]['intervals'] = [speech(0, 30, 'A'), speech(30, end, 'B')]
            with self.subTest(end=end):
                self.assertEqual(self.shots(self.plan(args)), expected)

    def test_unknown_cancels_delayed_candidate_then_rechecks_at_end(self):
        args = fixture()
        args[1]['intervals'] = [speech(0, 30, 'A'), speech(30, 150, 'B'), speech(45, 80, unknown=True)]
        self.assertEqual(self.shots(self.plan(args)), [(0, 80, 'CA'), (80, 200, 'CB')])

    def test_sustained_overlap_enters_at_start_with_minimum_exception(self):
        for end, expected in [(54, [(0, 30, 'CA'), (30, 90, 'W'), (90, 200, 'CA')]), (53, [(0, 200, 'CA')])]:
            args = fixture()
            args[1]['intervals'] = [speech(0, 200, 'A'), speech(30, end, 'B')]
            plan = self.plan(args)
            with self.subTest(end=end):
                self.assertEqual(self.shots(plan), expected)
                if end == 54:
                    self.assertIn('MIN_SHOT_EXCEPTION_OVERLAP', plan['segments'][1]['reasonCodes'])

    def test_unknown_frame_breaks_sustained_overlap_run(self):
        args = fixture()
        args[1]['intervals'] = [speech(0, 200, 'A'), speech(30, 70, 'B'), speech(50, 51, unknown=True)]
        self.assertEqual(self.shots(self.plan(args)), [(0, 200, 'CA')])

    def test_no_suitable_overlap_camera_holds_and_reviews_full_run(self):
        args = fixture()
        args[2]['cameras'][2]['coveredSpeakers'] = ['A']
        args[1]['intervals'] = [speech(0, 200, 'A'), speech(30, 70, 'B')]
        plan = self.plan(args)
        self.assertEqual(self.shots(plan), [(0, 200, 'CA')])
        self.assertTrue(any(r['code'] == 'OVERLAP_CAMERA_UNAVAILABLE' and r['startFrame'] == 30 and r['endFrame'] == 70 for r in plan['reviews']))

    def test_overlap_participant_change_rechecks_two_shot(self):
        args = fixture()
        args[2]['speakers']['C'] = 'CB'
        args[1]['sessionSpeakerIds'].append('C')
        args[2]['cameras'][2]['role'] = 'two-shot'
        args[1]['intervals'] = [speech(0, 200, 'A'), speech(30, 100, 'B'), speech(50, 100, 'C')]
        plan = self.plan(args)
        self.assertTrue(any(r['code'] == 'OVERLAP_CAMERA_UNAVAILABLE' and r['startFrame'] == 50 for r in plan['reviews']))

    def test_silence_preserves_existing_camera_and_audio_snapshot(self):
        args = fixture()
        args[1]['intervals'] = [speech(0, 100, 'A')]
        before = copy.deepcopy(args[0])
        self.assertEqual(self.shots(self.plan(args)), [(0, 200, 'CA')])
        self.assertEqual(args[0], before)

    def test_missing_confirmed_range_speaker_mapping_blocks_even_if_suppressed(self):
        args = fixture()
        args[1]['intervals'] = [speech(0, 200, 'A'), speech(90, 100, 'B')]
        del args[2]['speakers']['B']
        self.blocked(args, 'SPEAKER_MAPPING_REQUIRED')

    def test_missing_audio_and_analysis_failure_are_not_silence(self):
        args = fixture()
        args[1]['validAudioRanges'] = [dict(startFrame=0, endFrame=100), dict(startFrame=101, endFrame=200)]
        details = self.blocked(args, 'MISSING_AUDIO')
        self.assertEqual((details['startFrame'], details['endFrame']), (100, 101))
        args = fixture()
        args[1]['error'] = dict(code='MODEL_NOT_READY', message='Not loaded')
        self.blocked(args, 'MODEL_NOT_READY')

    def test_video_gap_falls_back_then_returns_after_minimum(self):
        args = fixture(200, {'CA': [(0, 100), (130, 200)], 'CB': [(0, 200)], 'W': [(0, 200)]})
        args[1]['intervals'] = [speech(0, 200, 'A')]
        self.assertEqual(self.shots(self.plan(args)), [(0, 100, 'CA'), (100, 160, 'W'), (160, 200, 'CA')])

    def test_current_clip_end_can_break_minimum_hold(self):
        args = fixture(200, {'CA': [(0, 30)], 'CB': [(0, 200)], 'W': [(0, 200)]})
        args[1]['intervals'] = [speech(0, 200, 'A')]
        plan = self.plan(args)
        self.assertEqual(self.shots(plan), [(0, 30, 'CA'), (30, 200, 'W')])
        self.assertIn('MIN_SHOT_EXCEPTION_COVERAGE', plan['segments'][1]['reasonCodes'])

    def test_one_frame_global_gap_reports_exact_missing_interval(self):
        args = fixture(200, {camera: [(0, 100), (101, 200)] for camera in ['CA', 'CB', 'W']})
        args[1]['intervals'] = [speech(0, 200, 'A')]
        details = self.blocked(args, 'VIDEO_COVERAGE_GAP')
        self.assertEqual((details['startFrame'], details['endFrame']), (100, 101))

    def test_unlisted_reserve_never_becomes_fallback(self):
        args = fixture(200, {'CA': [(0, 100)], 'CB': [(0, 100)], 'W': [(0, 100)], 'R': [(0, 200)]})
        args[2]['cameras'][3]['role'] = 'reserve'
        args[1]['intervals'] = [speech(0, 200, 'A')]
        self.blocked(args, 'VIDEO_COVERAGE_GAP')
        args[2]['fallbackOrder'] = ['R']
        self.assertEqual(self.shots(self.plan(args)), [(0, 100, 'CA'), (100, 200, 'R')])

    def test_fallback_prefers_wide_then_current_then_explicit_reserve(self):
        args = fixture(200, {'CA': [(0, 200)], 'CB': [(100, 200)], 'W': [(0, 200)], 'R': [(0, 200)]})
        args[1]['intervals'] = [speech(0, 90, 'A'), speech(90, 200, 'B')]
        args[2]['fallbackOrder'] = ['R']
        self.assertEqual(self.shots(self.plan(args)), [(0, 90, 'CA'), (90, 150, 'W'), (150, 200, 'CB')])
        args[2]['cameras'] = [camera for camera in args[2]['cameras'] if camera['cameraId'] != 'W']
        args[2]['startCameraId'] = 'CA'
        self.assertEqual(self.shots(self.plan(args)), [(0, 100, 'CA'), (100, 200, 'CB')])

    def test_shared_camera_does_not_cut_on_speaker_change(self):
        args = fixture()
        args[2]['speakers']['B'] = 'CA'
        args[1]['intervals'] = [speech(0, 90, 'A'), speech(90, 200, 'B')]
        self.assertEqual(self.shots(self.plan(args)), [(0, 200, 'CA')])

    def test_overrides_have_exact_boundaries_and_two_exceptions(self):
        args = fixture()
        args[1]['intervals'] = [speech(0, 200, 'A')]
        args[3]['overrides'] = [dict(startFrame=40, endFrame=50, cameraId='CB')]
        plan = self.plan(args)
        self.assertEqual(self.shots(plan), [(0, 40, 'CA'), (40, 50, 'CB'), (50, 200, 'CA')])
        self.assertIn('MIN_SHOT_EXCEPTION_OVERRIDE', plan['segments'][1]['reasonCodes'])
        self.assertIn('MIN_SHOT_EXCEPTION_OVERRIDE_END', plan['segments'][2]['reasonCodes'])

    def test_override_camera_gap_blocks_without_fallback(self):
        args = fixture(200, {'CA': [(0, 200)], 'CB': [(0, 45), (46, 200)], 'W': [(0, 200)]})
        args[3]['overrides'] = [dict(startFrame=40, endFrame=50, cameraId='CB')]
        details = self.blocked(args, 'OVERRIDE_COVERAGE_GAP')
        self.assertEqual((details['startFrame'], details['endFrame']), (45, 46))

    def test_conflicting_overrides_report_intersection(self):
        args = fixture()
        args[3]['overrides'] = [dict(startFrame=40, endFrame=70, cameraId='CA'), dict(startFrame=60, endFrame=90, cameraId='CB')]
        details = self.blocked(args, 'OVERRIDE_CONFLICT')
        self.assertEqual((details['startFrame'], details['endFrame']), (60, 70))

    def test_same_camera_overrides_merge_and_invalid_override_not_clipped(self):
        args = fixture()
        args[1]['intervals'] = [speech(0, 200, 'A')]
        args[3]['overrides'] = [dict(startFrame=40, endFrame=70, cameraId='CB'), dict(startFrame=60, endFrame=90, cameraId='CB')]
        self.assertEqual(self.shots(self.plan(args)), [(0, 40, 'CA'), (40, 90, 'CB'), (90, 200, 'CA')])
        for start, end in [(40, 40), (-1, 20), (190, 210)]:
            args[3]['overrides'] = [dict(startFrame=start, endFrame=end, cameraId='CB')]
            with self.subTest(start=start, end=end):
                self.blocked(args, 'INVALID_OVERRIDE')

    def test_source_file_boundary_is_kept_without_resetting_camera_timer(self):
        args = fixture(200, {'CA': [(0, 30), (30, 200)], 'CB': [(0, 200)], 'W': [(0, 200)]})
        args[1]['intervals'] = [speech(0, 50, 'A'), speech(50, 150, 'B')]
        plan = self.plan(args)
        self.assertEqual(self.shots(plan), [(0, 30, 'CA'), (30, 60, 'CA'), (60, 200, 'CB')])
        self.assertNotEqual(plan['segments'][0]['sourceClipInstanceKey'], plan['segments'][1]['sourceClipInstanceKey'])

    def test_touching_utterances_merge_but_silence_does_not(self):
        args = fixture()
        args[1]['intervals'] = [speech(0, 90, 'A'), speech(90, 100, 'B'), speech(100, 109, 'B')]
        self.assertEqual(self.shots(self.plan(args)), [(0, 90, 'CA'), (90, 200, 'CB')])
        args[1]['intervals'][2] = speech(101, 110, 'B')
        self.assertEqual(self.shots(self.plan(args)), [(0, 200, 'CA')])

    def test_initial_single_speaker_ignores_short_suppression(self):
        args = fixture()
        args[1]['intervals'] = [speech(0, 10, 'A')]
        self.assertEqual(self.shots(self.plan(args)), [(0, 200, 'CA')])

    def test_zero_frame_analysis_is_reviewed_without_shot(self):
        args = fixture()
        args[1]['intervals'] = [speech(0, 200, 'A'), speech(30, 30, 'B')]
        plan = self.plan(args)
        self.assertEqual(self.shots(plan), [(0, 200, 'CA')])
        self.assertTrue(any(r['code'] == 'ZERO_FRAME_ANALYSIS' for r in plan['reviews']))

    def test_rational_threshold_uses_ceiling_not_float_round(self):
        args = fixture()
        args[3]['shortTurn'] = '0.60000000000000001'
        args[1]['intervals'] = [speech(0, 90, 'A'), speech(90, 109, 'B')]
        self.assertEqual(self.shots(self.plan(args)), [(0, 200, 'CA')])

    def test_unsupported_source_time_mapping_and_preservation_block(self):
        args = fixture()
        args[0]['clips'][0]['speed'] = 2
        self.blocked(args, 'TIME_MAPPING_UNSUPPORTED')
        args = fixture()
        args[0]['supportFlags']['audioPreservationSupported'] = False
        self.blocked(args, 'PRESERVATION_UNSUPPORTED')
        args = fixture()
        args[0]['clips'][0]['supportFlags']['timeMappingSupported'] = False
        self.blocked(args, 'TIME_MAPPING_UNSUPPORTED')

    def test_claimed_camera_coverage_cannot_exceed_source_ticks(self):
        args = fixture()
        args[0]['clips'][0]['outTicks'] = '8467200000'
        self.blocked(args, 'SOURCE_RANGE_INVALID')

    def test_validator_detects_plan_tampering_and_wrong_snapshot(self):
        args = fixture()
        args[1]['intervals'] = [speech(0, 200, 'A')]
        plan = self.plan(args)
        self.assertIs(self.module.validate_plan(plan, args[0], args[2]), plan)
        plan['segments'][0]['endFrame'] = 199
        with self.assertRaises(self.error):
            self.module.validate_plan(plan, args[0], args[2])
        plan = self.plan(args)
        args[0]['snapshotHash'] = 'other-snapshot'
        with self.assertRaises(self.error) as result:
            self.module.validate_plan(plan, args[0])
        self.assertEqual(result.exception.code, 'SNAPSHOT_MISMATCH')

    def test_validator_rejects_registered_but_wrong_camera_source(self):
        args = fixture()
        args[1]['intervals'] = [speech(0, 200, 'A')]
        plan = self.plan(args)
        plan['segments'][0]['sourceClipInstanceKey'] = 'CB-0'
        # Rehash to isolate the camera membership check from the integrity check.
        hash_function = importlib.import_module('contentrium_cut.contract').canonical_hash
        plan['planHash'] = hash_function({k: v for k, v in plan.items() if k != 'planHash'})
        with self.assertRaises(self.error) as result:
            self.module.validate_plan(plan, args[0], args[2])
        self.assertEqual(result.exception.code, 'INVALID_PLAN')

    def test_global_analysis_review_is_preserved_without_fabricated_range(self):
        args = fixture()
        args[1]['reviews'] = [dict(code='DUPLICATE_CHANNELS', assetIds=['left', 'right'])]
        plan = self.plan(args)
        self.assertIn(dict(code='DUPLICATE_CHANNELS', assetIds=['left', 'right']), plan['reviews'])

    def test_overlap_review_survives_required_coverage_fallback(self):
        args = fixture(200, {'CA': [(0, 40)], 'CB': [(0, 200)], 'R': [(0, 200)]})
        args[2]['startCameraId'] = 'CA'
        args[2]['cameras'][2]['role'] = 'reserve'
        args[2]['fallbackOrder'] = ['R']
        args[1]['intervals'] = [speech(0, 200, 'A'), speech(30, 80, 'B')]
        plan = self.plan(args)
        self.assertTrue(any(r['code'] == 'OVERLAP_CAMERA_UNAVAILABLE' and r['startFrame'] == 30 and r['endFrame'] == 80 for r in plan['reviews']))

    def test_validator_rejects_protected_track_without_optional_mapping(self):
        args = fixture()
        args[1]['intervals'] = [speech(0, 200, 'A')]
        plan = self.plan(args)
        args[0]['tracks'][0]['protected'] = True
        with self.assertRaises(self.error):
            self.module.validate_plan(plan, args[0])

    def test_validator_requires_segment_reason(self):
        args = fixture()
        plan = self.plan(args)
        del plan['segments'][0]['reason']
        hash_function = importlib.import_module('contentrium_cut.contract').canonical_hash
        plan['planHash'] = hash_function({k: v for k, v in plan.items() if k != 'planHash'})
        with self.assertRaises(self.error):
            self.module.validate_plan(plan, args[0])

    def test_order_of_input_records_does_not_change_plan(self):
        args = fixture()
        args[1]['intervals'] = [speech(0, 90, 'A'), speech(90, 200, 'B'), speech(30, 42, 'B')]
        first = self.plan(args)
        args[1]['intervals'].reverse()
        args[2]['cameras'].reverse()
        args[0]['clips'].reverse()
        args[0]['tracks'].reverse()
        self.assertEqual(self.plan(args), first)

    def test_nonzero_range_and_source_trim_map_exact_source_ticks(self):
        args = fixture()
        args[0]['range'] = dict(startFrame=50, endFrame=150)
        args[0]['clips'][0]['inTicks'] = '846720000000'
        args[0]['clips'][0]['outTicks'] = '2540160000000'
        args[1]['intervals'] = [speech(0, 200, 'A')]
        plan = self.plan(args)
        self.assertEqual(self.shots(plan), [(50, 150, 'CA')])
        self.assertEqual(plan['segments'][0]['sourceIn'], '1270080000000')
        self.assertEqual(plan['segments'][0]['sourceOut'], '2116800000000')

    def test_short_option_off_keeps_minimum_constraint(self):
        args = fixture()
        args[3]['suppressShort'] = False
        args[1]['intervals'] = [speech(0, 30, 'A'), speech(30, 48, 'B')]
        self.assertEqual(self.shots(self.plan(args)), [(0, 200, 'CA')])

    def test_wide_tie_uses_priority_then_camera_id(self):
        args = fixture(200, {'CA': [(0, 200)], 'CB': [(0, 200)], 'Z': [(0, 200)], 'D': [(0, 200)]})
        args[2]['startCameraId'] = 'CA'
        for camera in args[2]['cameras'][2:]:
            camera.update(role='wide', coveredSpeakers=['A', 'B'])
        args[1]['intervals'] = [speech(0, 200, 'A'), speech(30, 54, 'B')]
        self.assertEqual(self.shots(self.plan(args))[1], (30, 90, 'D'))
        args[2]['cameras'][2]['priority'] = -1
        self.assertEqual(self.shots(self.plan(args))[1], (30, 90, 'Z'))

    def test_offline_disabled_and_muted_camera_are_unavailable(self):
        for field in ['offline', 'disabled', 'muted']:
            args = fixture()
            args[1]['intervals'] = [speech(0, 200, 'A')]
            if field == 'offline':
                args[0]['sources'][0]['online'] = False
            elif field == 'disabled':
                args[0]['clips'][0]['disabled'] = True
            else:
                args[0]['tracks'][0]['muted'] = True
            with self.subTest(field=field):
                self.assertEqual(self.shots(self.plan(args)), [(0, 200, 'W')])

    def test_validator_rejects_rehashed_gap_overlap_and_source_tick_drift(self):
        args = fixture()
        args[1]['intervals'] = [speech(0, 90, 'A'), speech(90, 200, 'B')]
        hash_function = importlib.import_module('contentrium_cut.contract').canonical_hash
        for mutation in ['gap', 'overlap', 'source']:
            plan = self.plan(args)
            if mutation == 'gap':
                plan['segments'][1]['startFrame'] = 91
            elif mutation == 'overlap':
                plan['segments'][1]['startFrame'] = 89
            else:
                plan['segments'][0]['sourceOut'] = '762048000001'
            plan['planHash'] = hash_function({k: v for k, v in plan.items() if k != 'planHash'})
            with self.subTest(mutation=mutation), self.assertRaises(self.error):
                self.module.validate_plan(plan, args[0], args[2])

    def test_missing_start_camera_and_unregistered_sources_block(self):
        args = fixture()
        args[2]['startCameraId'] = 'unconfirmed'
        self.blocked(args, 'START_CAMERA_REQUIRED')
        args = fixture()
        args[2]['cameras'][0]['clips'][0]['instanceKey'] = 'unregistered'
        self.blocked(args, 'SOURCE_RANGE_INVALID')

    def test_nonfinite_duration_and_malformed_analysis_are_typed_errors(self):
        for duration in [-1, 'NaN', float('inf'), True]:
            args = fixture()
            args[3]['minShot'] = duration
            with self.subTest(duration=duration):
                self.blocked(args, 'INVALID_POLICY')
        args = fixture()
        args[1]['intervals'] = [speech(10, 9, 'A')]
        self.blocked(args, 'INVALID_ANALYSIS')

    def test_range_end_short_shot_is_recorded_without_extending_range(self):
        args = fixture(100)
        args[1]['intervals'] = [speech(0, 70, 'A'), speech(70, 100, 'B')]
        plan = self.plan(args)
        self.assertEqual(self.shots(plan), [(0, 70, 'CA'), (70, 100, 'CB')])
        self.assertIn('RANGE_END_SHORT', plan['segments'][-1]['reasonCodes'])

    def test_boolean_schema_version_is_not_integer_schema_one(self):
        args = fixture()
        args[0]['schemaVersion'] = True
        with self.assertRaises(self.error):
            self.plan(args)
        args = fixture()
        args[1]['schemaVersion'] = True
        with self.assertRaises(self.error):
            self.plan(args)

    def test_session_speaker_ids_must_be_explicit_list(self):
        args = fixture()
        args[1]['sessionSpeakerIds'] = 'AB'
        self.blocked(args, 'INVALID_ANALYSIS')

    def test_ntsc_policy_uses_exact_sequence_ticks_and_ceiling_thresholds(self):
        args = fixture()
        args[0]['fps'] = dict(num=30000, den=1001)
        for clip in args[0]['clips']:
            clip['endTicks'] = '1695133440000'
            clip['outTicks'] = '1695133440000'
        args[1]['intervals'] = [speech(0, 30, 'A'), speech(30, 120, 'B')]
        plan = self.plan(args)
        self.assertEqual(self.shots(plan), [(0, 60, 'CA'), (60, 200, 'CB')])
        self.assertEqual(plan['segments'][0]['sourceOut'], '508540032000')
        self.assertEqual(plan['segments'][1]['sourceIn'], '508540032000')

    def test_gap_in_desired_camera_cannot_break_current_minimum(self):
        args = fixture(200, {'CA': [(0, 200)], 'CB': [(100, 200)], 'W': [(0, 200)]})
        args[1]['intervals'] = [speech(0, 30, 'A'), speech(30, 120, 'B')]
        self.assertEqual(self.shots(self.plan(args)), [(0, 60, 'CA'), (60, 200, 'W')])

    def test_sustained_overlap_uses_full_analysis_before_range_clipping(self):
        args = fixture()
        args[2]['startCameraId'] = 'CA'
        args[0]['range'] = dict(startFrame=10, endFrame=25)
        args[1]['intervals'] = [speech(0, 200, 'A'), speech(0, 30, 'B')]
        self.assertEqual(self.shots(self.plan(args)), [(10, 25, 'W')])

    def test_overlap_crossing_range_end_enters_at_known_start(self):
        args = fixture()
        args[0]['range'] = dict(startFrame=0, endFrame=40)
        args[1]['intervals'] = [speech(0, 200, 'A'), speech(30, 100, 'B')]
        self.assertEqual(self.shots(self.plan(args)), [(0, 30, 'CA'), (30, 40, 'W')])

    def test_gap_review_continues_across_short_overlap_and_minimum_hold(self):
        args = fixture(200, {'CA': [(0, 100)], 'CB': [(0, 200)], 'R': [(0, 200)]})
        args[2]['startCameraId'] = 'CA'
        args[2]['cameras'][2].update(role='reserve', coveredSpeakers=[])
        args[2]['fallbackOrder'] = ['R']
        args[1]['intervals'] = [speech(0, 200, 'A'), speech(110, 115, 'B')]
        plan = self.plan(args)
        self.assertEqual(self.shots(plan), [(0, 100, 'CA'), (100, 200, 'R')])
        self.assertEqual([r for r in plan['reviews'] if r['code'] == 'VIDEO_GAP_FALLBACK'], [dict(code='VIDEO_GAP_FALLBACK', requestedCameraId='CA', cameraId='R', startFrame=100, endFrame=200)])

    def test_speaker_mapping_scope_excludes_other_session_speakers_outside_range(self):
        args = fixture()
        args[0]['range'] = dict(startFrame=0, endFrame=100)
        args[1]['intervals'] = [speech(0, 100, 'A'), speech(150, 200, 'B')]
        del args[2]['speakers']['B']
        self.assertEqual(self.shots(self.plan(args)), [(0, 100, 'CA')])

    def test_speaker_mapping_scope_uses_half_open_range_boundaries(self):
        args = fixture()
        args[0]['range'] = dict(startFrame=50, endFrame=100)
        args[1]['intervals'] = [speech(0, 50, 'B'), speech(50, 100, 'A'), speech(100, 200, 'B')]
        del args[2]['speakers']['B']
        self.assertEqual(self.shots(self.plan(args)), [(50, 100, 'CA')])

    def test_gap_review_stops_at_source_recovery_even_while_minimum_holds(self):
        args = fixture(200, {'CA': [(0, 100), (130, 200)], 'CB': [(0, 200)], 'R': [(0, 200)]})
        args[2]['startCameraId'] = 'CA'
        args[2]['cameras'][2].update(role='reserve', coveredSpeakers=[])
        args[2]['fallbackOrder'] = ['R']
        args[1]['intervals'] = [speech(0, 200, 'A'), speech(110, 115, 'B')]
        plan = self.plan(args)
        self.assertEqual(self.shots(plan), [(0, 100, 'CA'), (100, 160, 'R'), (160, 200, 'CA')])
        self.assertEqual([r for r in plan['reviews'] if r['code'] == 'VIDEO_GAP_FALLBACK'], [dict(code='VIDEO_GAP_FALLBACK', requestedCameraId='CA', cameraId='R', startFrame=100, endFrame=130)])


if __name__ == '__main__':
    unittest.main()
