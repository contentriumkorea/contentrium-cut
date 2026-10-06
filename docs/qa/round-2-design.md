# Contentrium CUT 2차 검수와 보완 설계

대상 배포: 0.1.1. 원본 상세 설계를 축소하지 않는다. 1차 공개 배포와 실제 Adobe 등록/설치된 0.1.0 HTTP health를 확인한 뒤 작성했다. 설치된 패널의 화면 실행 확인은 별도 관문이다.

## 검수에서 확인된 문제

| ID | 누락 또는 결함 | 2차 구현과 검증 |
|---|---|---|
| U1 | Adobe 사용자 DB가 시스템 DB의 ProductID를 참조한다. 0.1.0 frozen 설치/런처는 등록 판정 실패 | 읽기 전용 DB 교차 해석, 모호한 ID 차단, 실제 등록 확인, 0.1.1 frozen 재설치/런처 교체 |
| A1 | 같은 asset의 서로 다른 클립 인스턴스가 payload에서 덮어써짐 | instanceKey별 디코딩·trim·origin과 실제 공백 유지 |
| A2 | 한 사람의 분할 마이크 파일을 같은 화자로 선택할 수 없음 | 인스턴스 활동을 session speaker로 합치고 segment calibration 구별 |
| A3 | 완료 캐시의 출력 digest/shape 검증 없음 | schema/kind/key/result digest envelope, 변조·잘못된 범위·미완료 캐시 무시 |
| A4 | 강제 worker 종료 후 decoder 자식이 남을 수 있음 | Windows Job Object에 시작 장벽 전에 할당, kill-on-close, 실제 자식/손자와 FFmpeg 취소 시험 |
| A5 | 화자 병합·구간 재지정·되돌리기·지속 교정 없음 | 원시 분석 보존, revision이 있는 correction artifact, 서비스와 패널 편집/이력 |
| A6 | 긴 혼합 파일의 chunk/session identity 연결 없음 | bounded chunk 처리, 겹침 소유권, 근거 있는 ID 연결과 모호한 연결 수동 검토 |
| A7 | 디스크/메모리 사전 검사 없음 | PCM 크기/여유 공간·분석 메모리 사전 검사, typed error, 사용자 캐시 예산 |
| S1 | 싱크 stream/channel 선택·수동 근거·타임코드 경로 없음 | per-source stream/channel, 수동 대응 offset 확인, clock/FPS/DF/day/reset 검증 후 타임코드 offset |
| H1 | 컷 apply가 control.check 없이 실행 가능, 저장 뒤 원본 재조회 없음 | 필수 lease 체크·native opaque clip/proxy/transition 차단·저장 뒤 원본/결과 검증 |
| H2 | 프로젝트 패널 선택 파일 시작 경로 없음 | 선택된 imported media에서 새 입력 시퀀스 구성·지원 검사·원본 파일 보존 |
| H3 | apply 상세 batch journal 및 재시도 완료 receipt 부족 | durable requestId/planHash/sequence/batch receipt, 완료 요청 멱등 재조회, 부분 결과 자동 재사용 차단 |
| P1 | 일반 작업의 panel/runtime bundle/protocol 불일치 차단 부족 | health/heartbeat 계약과 모든 mutation admission에서 같은 bundle 요구 |
| P2 | 프리셋/매핑 영속화 없음, 작은 글자·고정 연결 표시 | project-scoped 설정, invalidation, 13px 이상 본문, 실제 연결 상태 표시 |

## 구현 순서와 소유권

1. 작업 취소/캐시 모듈: jobs.py, process_scope.py, cache.py 및 관련 tests. 다른 파일의 public worker/job interface는 유지한다.
2. 오디오/교정/싱크 모듈: audio.py, coordinator.py, corrections.py, resource.py 및 관련 tests. service endpoint와 패널은 다음 단계에 연결한다.
3. 서비스/설치: service.py, windows_install.py, integration.py, installer 및 tests. source fix를 frozen 두 패키지에 포함한다.
4. 패널/호스트: main.js/index.html/style.css/host.js 및 Node tests. 새 서비스 계약을 연결하고 실제 Premiere 결과를 검사한다.
5. 독립 review와 전체 회귀, 실제 frozen probe, signed 0.1.1 공개 배포, Premiere 정상 종료, 실제 재설치 및 설치된 패널 확인.

각 단계는 실패 재현 → 구현 → 관련 시험 → 독립 검수 순서로 기록한다. 구현 담당 agent는 한 번에 하나만 배정하고 root는 별도의 호스트/화면 검증을 수행한다. 이미 직접 승인된 설치/배포는 다시 승인 요청하지 않는다.

## 경계와 합격 기준

- Community-1 가중치는 제공자 접근 동의/권한이 필요하다. adapter 시험을 실제 모델 추론 성공으로 표기하지 않는다.
- 원본 미디어/시퀀스, 비선택 오디오와 오버레이는 보존한다. 사용자 프로젝트나 다른 Adobe 플러그인에는 시험 조작을 하지 않는다.
- 호스트 지원은 실제 검증된 Premiere 26.5.2. 다른 버전은 확인 없이 적용하지 않는다.
- 공개 payload는 한 번 공개한 뒤 교체하지 않는다. 설치 실패 기록은 숨기지 않는다.
- 0.1.1 실제 설치와 GitHub 서명/asset hash 검증이 끝나야 2차를 완료로 표기한다.
- 3차에는 독립 검수 → 추가 설계/실제 수정 → 0.1.2 공개 배포만 진행한다. 로컬 0.1.1을 유지해 사용자 업데이트 시험을 남긴다.

## 전체 변경 검수 후 추가 보완

2026-10-06, 115개 변경 파일의 독립 소스 검수에서 다음 통합 결함을 재현했다. 아래 수정은 기존 원본 보존·즉시 중단·응답 시간·내구성 계약을 완성하며, 제품의 별도 기능을 추가하지 않는다.

| ID | 확인된 결함 | 보완 설계와 합격 조건 |
|---|---|---|
| FR1 | 싱크 입력과 분석 마이크 외 카메라의 내용 교체가 적용 허가를 받을 수 있음 | 서버가 관측한 원본 SHA를 작업·시퀀스·검토 결과에 묶는다. 싱크 결과 재조회/계획, 컷 계획, begin 및 첫 native batch 전에 필요한 미디어 전부를 취소 가능한 소유 작업에서 검증한다. 경로·크기·시각이 같은 교체와 begin 후 교체 모두 거부하며 클라이언트 SHA를 신뢰 근거로 사용하지 않는다. |
| FR2 | 매초 상태 조회와 분석 시작이 모델 전체 파일을 동기로 해시함 | 일반 상태 조회는 제한된 메타데이터만 반환한다. 실제 모델 사용 전 무결성 검증은 취소·종료를 추적하는 준비 작업에서 수행한다. 상태 재사용은 명확한 무효화 규칙을 가지며, 느린 저장장치에서도 개별 인증 요청 8초 제한과 연결 확인/취소/업데이트 응답성을 유지한다. |
| FR3 | 캐시 정리가 admission/auth/service 잠금을 잡고 오래 실행됨 | 짧은 진입 검사 후 소유된 취소 가능 작업에서 정리한다. 삭제/결과 반영 전에 현재 권한·epoch를 확인하고, 활성 작업과 교정/편집 기록은 보존한다. 별도 스레드의 중단 요청을 걸어 즉시 차단·제한된 drain·임의 gate 재개 방지를 검증한다. |
| FR4 | 정상 60분·1,800구간 계획이 5,404개 작업을 필요로 하나 기록 한도는 4,096임 | 컷과 범위 밖 보존 fragment를 포함한 실제 native 작업 수와 저널 용량을 일치시킨다. 일반적인 밀집 롱폼 계획을 지원하며, 지원 범위를 초과한 작업은 복제/편집 전 구체적인 제한 사유로 차단한다. 1회 허가·결과 영수증·중단 후 복구 계약은 유지한다. |
| FR5 | 기본 자원 설정에 없는 캐시 예산을 NaN으로 표시함 | 미설정 값을 명시적으로 처리하고, 표시한 기본/추천 값 또는 생략 정책을 서버와 일치시킨다. 전송 전 숫자를 검사하며 첫 설치 상태에서 장치 설정만 바꿔도 정상 저장돼야 한다. |

하나의 구현 담당자가 전체 묶음을 수정하고 기존 최종 검수자가 그 변경분을 재검수한다. 느린 연산을 단순히 잠금 밖의 미소유 스레드로 옮기거나 시간 제한을 늘리는 방식은 합격으로 보지 않는다. 실제 설치·모델 품질·Premiere 동작의 미실행 관문은 별도로 유지한다.
