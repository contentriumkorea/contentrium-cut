# Contentrium CUT

Premiere Pro에서 시퀀스의 오디오를 로컬로 분석하고, 화자에 따라 카메라를 전환하는 UXP 패널과 Windows Companion입니다. 자동 싱크는 원본을 보존한 새 시퀀스에 적용합니다.

## 설치와 실행

1. [GitHub Releases](https://github.com/contentriumkorea/contentrium-cut/releases)에서 `Contentrium-CUT-Setup.exe`를 내려받습니다.
2. Premiere Pro를 정상 종료하고 설치 파일을 실행합니다. 설치기는 서명된 업데이트 정보와 실제 다운로드의 해시를 검증합니다.
3. 시작 메뉴의 **Contentrium CUT**을 실행하고 Premiere의 **Window → UXP Plugins → Contentrium CUT** 패널을 엽니다.
4. Companion에 표시된 연결 코드를 패널에 입력하고 편집할 시퀀스를 연결합니다.
5. 화자별 마이크 또는 혼합 녹음을 선택하고, 화자와 카메라를 연결합니다. 편집안을 검토한 뒤 적용하면 복제 시퀀스가 생성됩니다.

현재 실제 호스트 검증 대상은 **Windows / Premiere Pro 26.5.2**입니다. 다른 호스트 버전은 검증 없이 편집을 허용하지 않습니다.

## 모델과 개인정보

화자별 마이크 모드는 포함된 Silero VAD를 사용합니다. 혼합 녹음의 Community-1 모델은 제공자의 계정 접근 권한과 이용 조건 동의가 필요합니다. Companion에서 Hugging Face 토큰과 고정 리비전을 입력해 설치하며 토큰을 저장하지 않습니다. 모델 설치 후 오디오 분석은 로컬에서 수행합니다. 원음·프로젝트·화자 정보는 GitHub에 전송하지 않습니다.

FFmpeg는 설치 중 제공자의 고정 버전 패키지를 사용자의 PC에 직접 내려받습니다. 콘텐츠리움의 배포 파일에는 FFmpeg 바이너리를 재배포하지 않습니다. 다운로드 출처·버전·해시·공급자 라이선스는 로컬에 보관합니다.

## 업데이트

패널과 Companion을 열면 같은 저장소의 게시된 업데이트를 확인합니다. 새 버전이 없으면 편집을 계속합니다. **업데이트**를 누르면 설치 전체의 새 작업을 차단하고 실행 중인 CUT 작업을 중단합니다. 다운로드와 검증 이후 Premiere 정상 종료를 기다려 Adobe 설치기로 패널을 교체합니다. 새 패널과 Companion의 연결이 확인되어야 작업 차단이 해제됩니다. 중단된 편집은 자동 재개하지 않습니다.

업데이트 정보는 Ed25519로 서명합니다. Windows 실행 파일의 Authenticode 서명과는 별개입니다.

## 검증 범위

[설계](docs/superpowers/specs/2026-10-05-premiere-speaker-cut-design.md), [호스트 컷·렌더 증거](docs/qa/native-host-proof.json), [영상·연결 오디오 싱크 증거](docs/qa/native-sync-proof.json), [실행 기록](docs/qa/execution-ledger.md)을 제공합니다. 합성 자료의 SDK 검증과 실제 사람의 목소리 구분 정확도는 구별합니다. 제공자 접근이 필요한 모델의 실제 추론 정확도는 접근 권한을 확보한 환경에서 별도 평가해야 합니다.

일반 속도·일반 미디어 클립을 첫 대상으로 합니다. 검증하지 않은 시간 재매핑·중첩·멀티캠·일부 프록시와 전환 효과 구성은 적용을 차단합니다. 드리프트가 있거나 근거가 부족한 싱크는 자동 적용하지 않습니다. 기본 기능은 영상 컷과 화자 선택이며 전사·자막·음성 복원은 기본 범위에 포함하지 않습니다.

## 개발

Python 3.12 환경에 `requirements.lock`을 설치하고 `PYTHONPATH=companion python -m unittest discover -s tests`를 실행합니다. CPU PyTorch 배포 인덱스는 `https://download.pytorch.org/whl/cpu`입니다. Node 테스트는 `npm test`입니다. 실제 Premiere 시험 자료·접근 토큰·게시 서명 개인키는 저장소에 포함하지 않습니다.

패키지 생성은 `tools/collect_licenses.py`, `tools/build_runtime.py`, `tools/build_setup.py` 순서입니다. 서명 개인키는 저장소 외부에 보관합니다. 제3자 라이선스와 귀속 자료는 `licenses/` 및 패널의 `vendor/`에 있습니다.
