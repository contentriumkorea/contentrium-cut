# Contentrium CUT — 남은 백그라운드 테스트와 컷 트랙 배치 검증

2026-10-06. 사용자의 `남은 테스트 진행` 지시에 따라 앞서 보류했던 13개를 실제 Windows에서 실행했다. 컴퓨터유즈 금지는 유지했고 Premiere·브라우저 화면 조작, 재설치·빌드·서명·공개·버전 변경은 하지 않았다. 시험 파일과 프로세스만 생성·정리했다.

## 실행 결과

| 실행 | 결과 | 근거와 범위 |
|---|---|---|
| 남겨 둔 Windows·모델 테스트 | **13/13 통과**, 22.369초, 실패·오류·skip 0 | 실제 Win32 프로세스 신원·시험 mutex 2개, Job Object/FFmpeg 하위 프로세스 9개, 실제 Silero 2개 |
| 실제 Windows 원본 파일 잠금 | **4/4 통과**, 1.003초, 실패·오류·skip 0 | 쓰기·이름 변경·삭제 차단, 검증 완료 후 적용 시점까지 핸들 유지, 1회 take, 취소 후 실제 프로세스 종료·잠금 해제, SHA 불일치 거부 |
| 최상단 컷 정리 회귀 | **5/5 통과**, 117.0338ms | 실제 `host.js`와 `mutation.js`를 실행하고 Adobe SDK 경계만 대역 처리. Premiere 실측 결과가 아님 |
| 변경 후 전체 JavaScript | **146/146 통과**, 17535.4383ms, 실패·취소·skip 0 | 위 5개와 기존 인증·패널·workflow·JS/Python 서비스 통합 포함 |

기존 Python 465개 통과 결과와 이번 13개는 서로 다른 실행이다. 478개를 한 번에 새로 실행했다고 표시하지 않는다. 이번 수정은 영상 출력 JavaScript와 관련 시험·문서이며 Python 제품 코드는 변경하지 않았다.

실제 실행한 13개:

1. `test_native_process_identity_is_not_confused_with_pid_reuse`
2. `test_native_named_mutex_excludes_second_updater_then_releases`
3. `test_assignment_failure_never_releases_worker_or_leaks_admission_state`
4. `test_assignment_start_barrier_prevents_work_until_parent_attaches_scope`
5. `test_cache_staging_cannot_delay_forced_descendant_shutdown`
6. `test_canceled_state_persistence_cannot_delay_native_termination`
7. `test_forced_cancel_drains_actual_ffmpeg_blocked_on_retained_pipe`
8. `test_forced_cancel_drains_actual_ffmpeg_with_stubborn_worker`
9. `test_forced_cancel_drains_real_child_and_grandchild`
10. `test_kernel_kill_on_close_drains_tree_after_controller_crash`
11. `test_native_child_cannot_break_away_from_owned_scope`
12. `test_real_silero_korean_sapi_speech_and_silence`
13. `test_real_silero_worker_completes_and_reuses_validated_cache`

Windows 하위 프로세스 시험은 실제 Job Object를 사용한다. 작업이 취소를 무시하거나 파이프가 막혔을 때의 종료, controller 비정상 종료 시 커널의 트리 정리, 별도 시험 프로세스의 생존을 확인했다. 제품의 설치된 백그라운드 서비스·Premiere 종료/업데이트를 실제 실행한 증거는 아니다.

Silero 모델은 기존 설치의 로컬 파일을 읽어 SHA-256을 검증했다. revision `1e261b036686cd0017d500ee96acd1c4ba572a9d`, ONNX SHA-256 `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`. Windows 한국어 음성 합성을 임시 WAV 파일로 출력하고 ONNX Runtime CPU로 판정했다. 스피커 재생·녹음·파일 업로드는 하지 않았다. 결과는 204,607 samples, 발화 192 frames, 구간 7개였으며 앞뒤 무음 판정을 통과했다. 실제 여러 사람의 겹말·Community-1 품질 합격을 뜻하지 않는다. 해당 설치 모델 폴더에는 Silero만 확인했다.

## 추가 사용자 요구 반영

사용자가 ‘직접 편집한 것처럼 컷이 나뉘고 사용할 소스가 가장 위의 트랙에 정리되어야 한다’고 명시했다. 이어 자막은 별도 자막 트랙, 로고·다른 편집은 사용자가 직접 한다고 확정했다.

- 결과 시퀀스의 모든 영상 트랙보다 위에 `Contentrium CUT` 트랙을 하나 만들고 사용할 소스 구간을 개별 클립으로 이어 놓는다.
- 기존 소스 인스턴스를 복제·트리밍하므로 원본 소스·In/Out·효과를 유지한 일반 클립이다. 렌더 파일·중첩·멀티캠으로 변환하지 않는다.
- 같은 최상단 트랙의 시퀀스 끝 이후를 임시 작업 위치로 사용한다. 최종 컷을 지우지 않고 정확히 식별한 임시 조각만 제거한다. 위에 빈 작업용 트랙이 추가로 남지 않는다.
- 원본 시퀀스·오디오·기존 비카메라 트랙의 데이터와 시간, 선택 범위 밖 카메라 조각을 보존한다. 기존 로고의 합성 순서를 새 결과 위로 자동 변경하지 않는다.
- 적용 완료 시 컷 수·프레임 경계·소스 In/Out·효과와 함께 최상단 트랙·이름·활성 상태를 재조회한다. 최상단 트랙 전체의 클립 수와 원래 시퀀스 길이를 대조하여 범위 밖 임시 조각의 잔류도 거부한다. 잘못된 트랙에 놓이거나 정리가 끝나지 않은 결과는 성공으로 보고하지 않는다.
- 트랙 이름 변경을 기존 임시 정리 트랜잭션에 포함하여 `3 × fragments + 4` batch 계산을 유지했다.

## 실패 기록 및 검증 한계

추가 파일 잠금 시험의 첫 실행은 1개 통과·3개 실패였다. 실패 원인은 시험에서 모든 `PermissionError`에 Windows 오류 32를 기대한 것이다. 직접 재현하니 CPython의 파일 열기는 CRT의 errno 13과 winerror 없음, 이름 변경·삭제는 errno 13과 winerror 32를 반환했다. 작업은 모두 실제로 거부됐다. 제품 코드를 바꾸지 않고 이 서로 다른 오류 경로를 명시한 뒤 4개 전부 통과했다. 최초 실패 로그/JSON은 보존했다.

컷 출력 시험은 확정된 사용자 요구를 기존 코드에 먼저 적용하여 4개 실패를 확인했다. 기존 코드는 선택 카메라 중 최고 트랙에 컷을 놓고 그 위에 빈 임시 트랙을 남겼으며, 다른 카메라 트랙으로 잘못 배치된 readback을 성공으로 허용했다. 수정 후 같은 4개와 전체 145개를 통과했다.

독립 검수에서 추가 P2 1개를 재현했다. SDK의 임시 삭제가 정상 반환하되 실제 삭제가 이뤄지지 않으면 성공 영수증은 컷 2개인데 실제 출력 트랙에는 4개가 남고 길이가 120→420프레임으로 늘었다. 전체 출력 트랙·기준 길이 확인을 추가했으며, 삭제 무효 회귀는 수정 전 1개 실패를 확인한 뒤 수정 후 집중 5/5·전체 146/146을 통과했다. 최초 145개 통과 기록도 별도 보존한다. 같은 검수자가 수정분을 재검수하여 **P2 해결·새 지적 없음·이번 소스 수정 승인**으로 판정했다. 실제 Premiere 검수는 이 승인에 포함하지 않는다.

실제 Premiere의 같은 트랙 간 복제·트리밍·정리·저장/재열기, 자막 트랙 유지, 클립 수동 이동·트리밍, 좁은 패널 동작은 아직 실측하지 않았다. 설치된 모델/서비스 자동 연결, Community-1 실제 겹말 품질, 0.1.1 설치·배포와 이후 0.1.2 배포도 남아 있다. 이번 통과 결과로 전체 납품을 완료 처리하지 않는다.

재현 runner와 원문 로그는 `.superpowers/sdd/2026-10-05-contentrium-cut-implementation/`에 보존한다: `remaining-native-tests.py`, `remaining-native-20261006T004332-97008.{log,json}`, `native-source-lease-tests.py`, `native-source-lease-result.{log,json}`(최초 실패), `native-source-lease-second-run.log`, `cut-output-confirmed-requirement-red.log`, `cut-output-green.log`, `cut-output-full-node.log`, `cut-output-cleanup-red.log`, `cut-output-cleanup-green.log`, `cut-output-final-node.log`.
