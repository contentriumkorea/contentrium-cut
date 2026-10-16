# 2026-10-17 설치 진행·실패 안내 개선 실행 기록

- 사용자 요청에 따른 단일 수정 작업이며 반복 자동화는 PAUSED 유지. 시작 HEAD66b78ce7231b39eaeb97d10bc08ff81f90d1194c, 공개 latest0.1.108/release414451842. 기존 설치0.1.107과 중요5파일은 읽기 전용 스냅샷으로 보존 확인한다.
- 사용자 보고: 다른 PC에서 일반 실패 안내가 반복되며 Premiere 종료 후에도 설치되지 않음. 전달된 `검증된 설치 창에서 계속해 주세요`는 인계 안내이므로 해당 PC의 구체적인 실패 원인은 아직 알 수 없다. 종료 상태가 원인이라고 단정하지 않는다.
- 확인·수정: full Setup의 오류가 온라인 진입점에서 숫자 종료 코드와 일반 문구로 소실됨. 구조화된 결과·코드·해결 안내·진단 복사·재시도를 연결했다. main 이전 import 실패를 잡는 얇은 entry wrapper와 읽기 전용 diagnose 추가. PyInstaller child의 부모 Tcl/Python 환경 제거는 방어 조치이며 사용자 PC 원인으로 확정하지 않음.
- UI: 온라인/전체 Setup 공통 모노 화면, 4단계, 실제 bytes 기반 퍼센트/용량/속도/ETA. 수치가 없는 검증·등록·첫 패널 연결은 임의 퍼센트 없음. 기본 Tk 깃털을 자체 C 아이콘으로 교체. 실제 native UI는 컴퓨터유즈 금지로 조작·시각 검수하지 않았다.
- 설계·수용 조건: `../superpowers/specs/2026-10-17-installer-feedback-design.md`, 실행 계획: `../superpowers/plans/2026-10-17-installer-feedback.md`.
- RED 증거: 초기 feedback7개, view3개, 온라인 worker 실패전달, FFmpeg 진행콜백 미구현. 추가 same-file 재시도 속도 초기화/비객체 결과 거부/취소된 패널 대기/진단 기본 경로를 재현하고 회귀로 고쳤다. 테스트 UTF-8 읽기를 cp949 기본으로 호출한 시험 코드 오류도 명시UTF-8로 수정. 로그는 `.superpowers/installer-feedback-20261017`에 보존.
- 독립 readonly review: installer_feedback_review. 중요2건: 최종 네트워크 확인 전에 handoff 상태가 되어 취소가 무시됨 / 진단 기본 임시 root가 실제 등록 위치 검사를 건너뜀. 실제 spawn 직전 callback과 최종 취소 확인, 명시된 진단 root만 전달하도록 수정. 관련 회귀 통과. 검토자는 실제 UI·설치를 실행하지 않았다.
- 환경 실패: 기존 launcher test의 실행하지 않는 짧은 텍스트 EXE fixture가 Defender1116에 격리되어 OSError22 발생. 이벤트의 해당 임시 new-setup.exe 경로와 재현을 확인했다. 임의 non-PE 테스트 바이트로 바꾸고 동적 서명·동일 복구 검증은 유지하여 관련99개 통과. 보안 기능·예외·정책을 변경하지 않았으며 실제 제품 EXE 탐지로 해석하지 않는다.
- Ruling: 선택적 진단 로그 경로에 쓸 수 없어도 부모 결과가 남도록 분리한다. RED→GREEN, 관련37개 통과. 중간 Setup/온라인 EXE는 로컬 증거 폴더에 이동 보존하고 새 파일을 만들었다. 이전 공개 파일 교체는 없다.
- Ruling: 읽기 전용 실제 등록 root 검사에서 120초 도우미 제한을 넘겼다. 도우미의 부모 timeout 뒤 자식 검증은 DIAGNOSED 결과를 남기고 종료했으며 잔류 자체 진단 프로세스 없음. 실제 설치 성공 증거로 해석하지 않는다. 이 검사가 창 생성 전에 동작하던 불편을 재현2개→worker로 이동→관련59개 통과로 보완했다. 검증을 우회하거나 사용자 파일을 교체하지 않는다.
- 최종 추가 readonly review: root 확인 전 설치 진입 불가, 위치 오류·일반 재시도/취소 안전 확인. Minor P3는 이전 위치 실패 후 재시도 결과가 이미 큐에 있는 바로 그 시점에 닫을 경우 이전 FAILED 진단이 남는 표시 경합이다. 파일 변경·설치 시작·성공 오표시는 없으므로 이번 범위에서 기존 실패 기록을 유지하고 후속 검토 항목으로 기록한다. 반복 작업은 재개하지 않는다.
- 온라인16,958,307바이트/전체Setup93,437,432바이트 최종 빌드. [진입점 검증](installer-0.1.109-proof.json): 실제 EXE 소스 모듈/entry 일치, PE아이콘7개와 창 PNG 일치, AI 엔진 없는 온라인 파일 확인. 온라인 frozen --check는 공개0.1.108 발견·서명 검증, 새 full Setup --diagnose는 isolated root에 쓰기 없이 exit0/DIAGNOSED. 최초 검증 helper의 archive 경로 slash/backslash 차이는 도우미에서 정규화하고 실패 로그 보존.
- 최종 Python572개/skip0/실패0, 실제 Silero2시험 포함, 144.833초. Node783 pass/0 fail, 126.535초. 앞선 Python569/skip2, 보완 뒤570/skip0, 최종 root 화면 보완 뒤572/skip0 순으로 실제 실행했으며 모든 실패 원인과 변경을 구분해 보존한다. 네이티브 UI·실제 사람 화자 품질 검증을 대신하지 않는다.
- 본체332,237,248바이트/CCX87,202바이트 생성. [압축형 검증](portable-0.1.109-proof.json): 332,687,340바이트, 원본18,119항목 CRC/크기 일치·같은 CCX·등록 필요 안내·빌더의 전체CRC 검사 통과.
- [최종 패키지 검사](package-0.1.109-proof.json): ZIP/CCX 전체CRC·공개정보 검사, 본체29개/Setup29개 모듈·2entry의 실제 frozen 소스 일치, CCX16파일 일치, 동봉Silero 해시 통과. 추출 본체 --probe runtimeReady=true/0.1.109, torch2.14.1+cpu/onnx1.30.0, Setup --help exit0, 124초. 실제 설치·Adobe등록·UI 조작 없음.
- 진행 단계: 구현·회귀·독립 리뷰·패키지 실행 검증 완료. 아직0.1.109 공개 전이며, 검증된 소스를 먼저 올리고 동일 자산으로 공개한다. 아래에 공개 다운로드·서명·고정v0.1.1·사용자 설치 불변 결과를 기록한다.
