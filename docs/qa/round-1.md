# Contentrium CUT 1차 구현·검증 기록

대상: 0.1.0 / Windows / Premiere Pro 26.5.2. 이 문서는 패키지 빌드와 실제 시험의 근거를 구별한다. 설치와 공개 배포 결과는 실제 완료 후 추가한다.

| 요구 | 구현 및 근거 | 제한/남은 검수 |
|---|---|---|
| 화자 중심 컷 | 결정 정책·검토 사유·카메라 매핑, 263 Python 및 33 Node 검증 | 한국어 다인 세션의 인식 품질 수치 미검증 |
| 분리/혼합 녹음 | 실제 Silero ONNX, Community-1 일반 diarization 어댑터 및 waveform 전달 | Community-1 계정 동의/권한과 실제 가중치가 없어 실추론 미검증 |
| 싱크 | 실제 FFmpeg 다중 창·연결 경로·드리프트 판정, native-sync-proof.json | 공통 타임코드 입력·수동 오프셋 UI 미구현 |
| Premiere 연동 | 실제 UXP SDK·복제·영상 컷·영상/오디오 이동 | 프로젝트 패널 선택 파일에서 시작하는 경로 미구현; 현재 시퀀스 경로 사용 |
| 원본/오디오 보존 | 실제 720프레임·3카메라 경계 재조회, 8장 native export 색 경계, linked audio 보존 | 60분 native 적용, keyframe/외부 효과/합성 오버레이 시험 미검증 |
| Mono Studio | 실제 흑백 UXP UI·범위·입력·매핑·규칙·검토·적용·모델·업데이트 | 매핑/프리셋 영속화 및 화자 병합/재지정 UI 미구현 |
| 로컬 실행 | Python 미설치 환경을 위한 frozen exe, actual imports와 spawned analysis job 통과 | Community-1 제공자 모델 설치는 사용자 동의 필요 |
| GitHub 업데이트 | 고정 저장소, Ed25519, SHA256/assetId/releaseId, 즉시 admission 중단, 정상 Premiere 종료 후 CCX 교체 | 실제 첫 설치·두 번째 버전 업데이트 인계 검증 필요 |
| 취소/복구 | owned worker·epoch·apply lease·단계별 원본/결과 검증, updater durable journal | 호스트 적용 자체의 상세 배치 journal/완료 결과 멱등 재조회 부족; forced-worker descendant 종료 추가 검수 |

첫 배포 전 발견/수정: RFC8785 JS/Python hash 불일치, Premiere 실제 host.version getter 차이, 연결 오디오 복제 부작용, callback-scoped selection, native source In 변경 시 시작 좌표 이동, 시퀀스 sync 승인 경로, updater job/receipt epoch race, handoff drain, plan 응답/매핑 경쟁. 개발 fixture UI와 자격증명은 배포에서 제외한다.

첫 설치 복구 검수: Adobe 등록 후 호스트가 다시 열리는 경로와 시작 메뉴/프로토콜 등록 실패가 재시도를 막는 문제를 독립 검수에서 발견했다. 수정 및 회귀 검증 후 설치/배포한다.

0.1.0은 검증된 현재 시퀀스 기반 핵심 경로의 초기 공개 시험판이다. 위 미구현·미검증 항목을 완료라고 표기하지 않으며, 2차/3차 검수에서 다시 추적한다.

첫 설치 복구 수정: durable signed/pinned install journal, Adobe 등록 전 intent, 같은 서명된 ZIP 내용과 CCX 재검증 후 인계, 실패한 시작 메뉴·프로토콜 등록의 offline repair. 독립 110개 관련 검증과 전체 263개 검증 통과.

실제 공개/설치: v0.1.0을 Contentrium CUT 제목으로 공개했다. frozen Setup의 Adobe 등록 후 교차 DB 등록 판정 실패를 확인했다. 원래 서명/다운로드/journal을 유지한 source installer 복구로 0.1.0을 설치했다. 실제 installed exe localhost health 0.1.0, UDT CUT Not loaded 상태에서 Premiere Window → UXP Plugins → Contentrium CUT 메뉴와 설치 패널 0.1.0을 확인했다. 패널을 실제 frozen engine에 연결하고 owned linked project의 현재 시퀀스 이름/30fps/780프레임 및 마이크·영상 트랙을 읽었다. frozen Setup/런처 결함을 통과로 기록하지 않는다. 0.1.1에서 두 frozen 프로그램을 재빌드해 교체한다.

공개 URL: https://github.com/contentriumkorea/contentrium-cut/releases/tag/v0.1.0
