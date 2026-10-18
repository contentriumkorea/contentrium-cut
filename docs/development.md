# 개발과 검증 자료

[사용자용 첫 화면](../README.md) · [설치·사용 안내](installation.md)

## 설계와 검증 자료

[상세 설계](superpowers/specs/2026-10-05-premiere-speaker-cut-design.md), [단일 패널 개편 설계](superpowers/specs/2026-10-06-contentrium-cut-single-panel-design.md), [1차 호스트 컷·렌더 증거](qa/native-host-proof.json), [1차 영상·연결 오디오 싱크 증거](qa/native-sync-proof.json), [실행 기록](qa/execution-ledger.md)을 제공합니다. 1차 증거는 개편된 패널의 실제 동작 증거를 대신하지 않습니다. 합성 자료의 SDK 검증과 실제 사람의 목소리 구분 정확도는 구별하며, Community-1의 실제 추론은 아직 검증하지 않았습니다.

일반 속도·일반 미디어 클립을 첫 대상으로 합니다. 검증하지 않은 시간 재매핑·중첩·멀티캠·일부 프록시와 전환 효과 구성은 적용을 차단합니다. 드리프트가 있거나 근거가 부족한 싱크는 자동 적용하지 않습니다. 기본 기능은 영상 컷과 화자 선택이며 전사·자막·음성 복원은 기본 범위에 포함하지 않습니다.

## 개발

Python 3.12 환경에 `requirements.lock`을 설치하고 `PYTHONPATH=companion python -m unittest discover -s tests`를 실행합니다. CPU PyTorch 배포 인덱스는 `https://download.pytorch.org/whl/cpu`입니다. Node 테스트는 `npm test`입니다. 실제 Premiere 시험 자료·접근 토큰·게시 서명 개인키는 저장소에 포함하지 않습니다.

패키지 생성은 `tools/collect_licenses.py`, `tools/build_runtime.py --silero-dir <검증된 모델 경로>`, `tools/build_setup.py`, `tools/build_online_setup.py`, `tools/distribution.py --version <버전>` 순서입니다. 모델 경로는 명시적으로 지정하며 공개 자산에 설치별 인증 정보가 있으면 빌드를 차단합니다. 이미 생성한 같은 버전의 배포 파일은 덮어쓰지 않습니다. 서명 개인키는 저장소 외부에 보관합니다. 제3자 라이선스와 귀속 자료는 `licenses/` 및 패널의 `vendor/`에 있습니다.

## 이전 배포 연결 보존

기존 업데이터는 선택한 릴리스 ID·버전·자산과 `target_commitish`를 저장하고 다시 확인합니다. 진행 중인 업데이트가 선택한 이전 릴리스를 숨기면 재확인과 고정 다운로드 주소가 끊길 수 있습니다. 고정 `v0.1.1` 전환도 원래 릴리스의 식별 정보를 요구합니다.

따라서 사용자 다운로드 진입점은 최신 정식 버전으로 유지하되, 이미 배포한 설치·업데이트·복구 연결을 보호하기 위해 이전 릴리스를 공개로 보존합니다. 기존 사용자 연결 보호가 최신 하나만 공개하는 기본값보다 우선합니다. 릴리스·첨부파일·태그·커밋을 삭제하거나 기존 파일·식별 정보를 교체하지 않습니다.

[제3자 라이선스와 귀속 자료](../licenses/)를 유지합니다.
