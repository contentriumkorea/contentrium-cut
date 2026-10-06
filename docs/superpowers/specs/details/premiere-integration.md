# Contentrium CUT — Premiere UXP 연동 상세 계약

기준일: 2026-10-05. [상위 설계](../2026-10-05-premiere-speaker-cut-design.md)의 연동 부록. **실제 연동은 미검증**이다. 기존에 확인한 Windows Premiere 26.5.2가 첫 대상이며 이번 작업은 실행·설치·프로젝트 변경 없는 공식 문서 조사다.

2026-10-06 개편: 설치별 비공개 bootstrap을 통한 자동 상호 인증과 숨겨진 실행 구조는 [단일 패널 재설계](../2026-10-06-contentrium-cut-single-panel-design.md)를 따른다. 초기 문서 작성 이후의 실제 검증은 [검수 기록](../../../qa/round-2.md)에서 확인한다.

## 1. 제품과 입력 경계

Contentrium CUT은 Mono Studio 스타일을 적용한 Manifest v5, `host.app="premierepro"`의 도킹 UXP panel이다. 실제 `require("premierepro")` 결과를 표시하고 Premiere와 companion 연결을 나눈다. 선언된 최소 버전 이상의 모든 빌드를 검증했다고 표시하지 않는다. 브라우저 시안·모의 응답은 연동 증거가 아니다. [Manifest][manifest]

`rawsync`는 가져온 ProjectItem으로 일반 트랙 싱크 시퀀스를 생성하고, `synced`는 기존 시퀀스를 사용한다. 전체 복제 후 영상만 편집하며 원본·오디오·효과·오버레이·자막·범위 밖·전체 길이를 보존한다. 1배속 순방향만 지원하고 중첩·멀티캠·병합·역재생·램핑·복잡한 조정 레이어는 차단한다. 이는 제품 범위이며 Adobe 기능 부재 선언은 아니다.

`Project.getActiveProject()/getActiveSequence()`를 읽고 `ProjectUtils.getSelection(project)` 또는 `getSelectionFromViewId()` 뒤 `getItems()`로 선택을 확정한다. 뷰의 프로젝트 소속을 확인하며 분석 중 입력을 교체하지 않는다. [Project][project] · [ProjectUtils][projectutils]

프로젝트/시퀀스 GUID, ProjectItem `getId()`, 트랙 ID/인덱스·`getTrackItems(CLIP,false)`·TRANSITION을 읽는다. 인스턴스 네 경계·속도·역재생·disabled·소스, `ClipProjectItem.cast()` 후 경로·프록시·오프라인·특수 유형을 확인한다. 트랙 `isMuted()`도 읽으며 기존 muted/disabled 카메라를 자동으로 켜서 커버리지를 채우지 않는다. [Sequence][sequence] · [VideoClip][vclip] · [Source][source]

영속 clip GUID는 미확정이다. `instanceKey`를 발급하고 트랙+소스 ID+네 경계+읽을 수 있는 효과로 재매칭하며 중복 후보는 중단한다. 재열기 후 핸들을 버리고 소스 ID 안정성도 시험한다. channelMap/effectFingerprint는 값과 complete/partial/unknown 상태를 구별하며 unknown끼리 같다고 판정하지 않는다.

## 2. 소스·채널·시간 계약

ProjectItem은 공유 소스, TrackItem은 시퀀스 인스턴스다. 복제 후에도 공유 소스를 쓰므로 원본 In/Out·FPS 해석·LUT·미디어 시작·경로는 임시 변경도 금지한다. `changeMediaFilePath()/attachProxy()`는 Undo 불가다. 실행기는 소유한 결과 영상 인스턴스와 신규 항목만 변경한다. [Source][source]

파일 채널·클립 매핑·믹서 출력은 다르다. 임의 채널 매핑·링크 그룹·믹서 전체 조회는 미확정이며 SequenceSettings 채널 수로 대체하지 않는다. companion의 ffprobe streamIndex/channelIndex/sampleRate/layout과 음성 샘플로 사용자가 확인한다. 분석 선택은 Premiere 설정을 변경하지 않고 ‘믹서 출력 분석’으로 표시하지 않는다.

시간은 zero point·표시 TC와 분리한 프레임/tick 문자열이다. `getTimebase()/TickTime.ticks`를 읽고 `createWithTicks()`로 전달한다. FPS 분수·프레임당 tick을 대조하며 float 초를 누적하지 않는다. 아래쪽 `alignToFrame()`과 최근접 `alignToNearestFrame()`을 구별한다. [TickTime][time]

1배속은 `sourceTick=sourceInTick+(sequenceTick-instanceStartTick)`이다. 정상 `getSpeed()` 값은 기준 클립으로 교정하며 해석·time-remapping·길이까지 검증한다. `Media.getStart()`와 디코더 PTS 원점은 같다고 가정하지 않는다. VFR·프록시·FPS 재해석·램핑 판별 불가는 차단한다.

## 3. 기능 게이트

아래 G01–G09는 메인의 제품 관문 G1–G6와 별개인 호스트 기능 게이트다. documented/hostPassed/evidenceId/failureReason을 저장하고 실측 통과 후 활성화한다.

| 게이트 | 문서화된 수단 | 실제 통과 조건 |
| --- | --- | --- |
| G01 입력 | Project/ProjectUtils/트랙/소스 조회 | 선택 프로젝트·파일·채널 일치, 누락/오프라인 차단 |
| G02 복제 | `Sequence.createCloneAction()` [Sequence][sequence] | 새 GUID 1개 식별, 설정·오디오·효과·범위 밖 동일 |
| G03 rawsync | `createSequenceWithPresetPath()`(26.3+), insert/overwrite [Project][project] [Editor][editor] | 트랙·소스·시간·최종 오디오 선택을 readback |
| G04 분할 | `createCloneTrackItemAction()` 및 인스턴스 In/Out/Start/End Action [Editor][editor] [VideoClip][vclip] | 영상만 분할, 오디오·검증된 링크 관계 불변, 동일 소스시점 효과·키프레임 유지 |
| G05 전환 | `createSetDisabledAction(bool)` [VideoClip][vclip] | 선택 영상만 표시, 오디오 불변, 기존 disabled 임의 복구 금지 |
| G06 제거 | `createRemoveItemsAction(selection,ripple,mediaType,shiftOverLapping)` [Editor][editor] | VIDEO·비리플·비이동으로 영상만 제거. 정상 컷은 disabled 우선 |
| G07 효과 | `getComponentChain()`, component/param 조회 [Params][params] | 순서·값·키프레임·실제 화면 일치. 불명 효과는 차단 |
| G08 수명 | lockedAccess/executeTransaction [Locks][locks] | 취소·실패·Undo 후 부분 결과 식별 |
| G09 연결 | network/shell [Network][network] [Launch][launch] | 실제 UXP에서 인증·취소·재연결 통과 |

직접 razor/split API는 미확정이다. `SequenceOperation.APPLYCUT`·`SnapEvent.RAZOR_PLAYHEAD` 상수나 멀티캠 감지를 편집 API로 오인하지 않는다. QE·UI 클릭·ExtendScript 우회는 채택하지 않는다. [Constants][constants]

## 4. 기존 시퀀스 적용 후보와 fallback

G02에서 전체 복제 후 `getSequences()` GUID 차이와 구조를 대조한다. clone Action은 Sequence를 직접 반환하지 않으므로 새 후보가 정확히 1개여야 한다. 소유 GUID 기록 후 해당 ProjectItem rename Action으로 작업중 이름을 지정한다.

G04는 **복제본의 빈 끝부분에서 clone+trim**을 시험한다. `[s,e)↔[p,q)`를 b에서 나누면 목표는 왼쪽 `[s,b)↔[p,p+b-s)`, 오른쪽 `[b,e)↔[p+b-s,q)`다.

1. 전체 끝 뒤 빈 위치로 영상 인스턴스를 복제한다. 인수는 원본 대비 offset이며 `isInsert=false`다. 예상치 않은 오디오 복제·다른 트랙 이동은 즉시 실패다.
2. 기존 왼쪽과 복제 오른쪽의 네 경계를 맞춘다. setter 순서가 교환 가능하거나 razor와 같다고 가정하지 않는다. 소스/타임라인 경계의 상호 작용을 시험해 고정한 순서만 사용한다.
3. 오른쪽을 b로 이동하고 경계·소스 프레임·효과를 재조회한다. move Action의 절대값/offset 의미도 시험으로 확정한다. 임시 구간이 비고 전체 길이가 복원되어야 한다.
4. 2026-10-06 확정 출력은 구간별 실제 클립을 모든 영상 트랙 중 최상단의 새 `Contentrium CUT` 트랙에 모으는 방식이다. 선택 범위의 카메라 원본 조각은 결과 복제본에서 교체하고 범위 밖 조각은 원래 트랙에 유지한다. 임시 조각도 같은 최상단 트랙의 시퀀스 끝 이후에 두며, 정확히 식별한 임시 복제본만 정리한다. 기존 비카메라 트랙과 오디오는 변경하지 않는다. 자막은 별도 자막 트랙이며 로고·그래픽의 최종 배치·합성은 사용자가 편집한다. 이 출력 계약은 위 G05의 비활성 토글 후보와 이전 상단 오버레이 배치 제안보다 우선한다.

끝쪽 임시 배치는 작업중 길이를 늘릴 수 있다. 모든 원본 소스 범위를 지키고, 완료 시 임시 조각 0·기준 길이·범위 밖 구조를 재검증한다. track-matte·다른 카메라와의 blend처럼 disabled가 합성에 영향을 주는 구성은 보존 검증 전 차단한다. EditPlan은 표시 화면 구간이다. 실행기가 컴파일한 물리 조각/disabled 상태를 별도로 검증하며, 숨겨진 조각을 화면 커버리지나 UI 전환 수로 세지 않는다.

이는 **호스트 실현 가능성 시험안**이다. Adobe offset 복제 샘플도 효과·오디오 보존을 보증하지 않는다. 영상만 복제되지 않거나 키프레임·마스크가 변하면 실패하며 생긴 오디오를 삭제해 성공 처리하지 않는다. 독립 오디오 시험은 linked clip 지원 증거가 아니다. 입력의 링크·효과 구성이 통과한 지원 구성인지 식별할 수 없으면 분석/계획까지만 허용한다. [Adobe 샘플][sample]

실패하면 복제본·편집안만 보존하고 적용을 막는다. 사전 분할된 입력은 G05로 제한 처리할 수 있지만 완전 자동 컷 완료는 아니다. 효과 누락 재삽입·렌더 영상 대체는 금지한다.

## 5. rawsync 시퀀스 구성

offset·범위·근거에 따라 검증 preset의 빈 일반 시퀀스에 카메라별 배치한다. `createSequenceFromMedia()` 자체는 싱크가 아니다. 이동 방지를 위해 overwrite를 우선하며 insert의 신규 트랙 생성도 시험한다. [Editor][editor]

카메라 현장음은 싱크용과 최종 출력용을 분리한다. 원본 In/Out·채널을 바꾸지 않고 필요하면 `createSubClipAction(name,start,end,hard,{takeVideo,takeAudio})`(26.3+)로 작업 소유의 영상/오디오 전용 하위 클립을 만들어 삽입하는 경로를 시험한다. 신규 항목 식별·길이·소스 효과/해석 상속·채널 보존이 조건이다. `-1` 트랙 인덱스가 미디어 제외를 뜻한다고 추정하지 않는다. 미검증이면 기존 싱크 시퀀스 경로를 안내하고 rawsync 자동 생성은 보류한다. [Source][source]

모든 파일을 배치/사용자 제외/확인 필요로 기록하고 녹화 공백을 유지한다. 선택 음원을 1배속 배치한 싱크 시퀀스를 검증한 뒤 기준으로 확정하며, 이후 오디오 배치·볼륨·효과를 보존한다.

## 6. 잠금과 변경 감지

26.3+에서는 `project.lockedAccess` 안에서 Action을 생성하고 `executeTransaction`의 compound에 넣는다. 콜백의 await·Promise·I/O·네트워크·모델 계산과 Action의 범위 밖 보관은 금지한다. 분석·계획은 잠금 밖이다. [Changelog][changes] · [Locks][locks]

비동기 getter를 잠금 안에서 await하지 않는다. 이벤트 epoch 전후 비교와 안정된 두 스냅샷을 확보하고 잠금 직전 GUID·hash·epoch를 확인한다. 잔여 경쟁은 복제본 비교·단계별 readback으로 탐지한다. 원본 변경은 계획을 무효화하며 사용자 편집을 되돌리지 않는다.

`EventManager.addEventListener/addGlobalEventListener`로 Project OPENED/CLOSED/ACTIVATED/DIRTY/PROJECT_ITEM_SELECTION_CHANGED, Sequence ACTIVATED/CLOSED/SELECTION_CHANGED, 트랙 변경·잠금 변경을 구독한다. 대상 조합은 실측한다. 이벤트는 revision이 아닌 재조회 신호다. debounce·자체 이벤트 구분·listener 해제, 활성화/재연결/적용 직전 재조회가 필요하다. 사용자 변경 시 다음 안전 경계에서 중단한다. [Events][events] · [Constants][constants]

## 7. 취소·Undo·멱등성·복구

`executeTransaction()`의 Undo/boolean은 전체 작업 원자성·크래시 rollback 보장이 아니다. 비동기 조회가 필요한 단계는 분리한다. 각 batch에 Undo 이름을 주며 한 번의 Undo로 전체 취소를 약속하지 않는다. 자동 Ctrl+Z는 사용자 작업을 취소할 수 있어 금지한다. [Project][project]

APPLYING(내부 생성 단계 STAGING)→VERIFYING→COMPLETED를 따른다. 적용 중단은 INTERRUPTED_OUTPUT, 적용 전 실패/취소는 FAILED/CANCELED다. 취소는 짧은 transaction 사이에서 수용하며 batch 크기는 실측한다. 검증 전 결과를 성공으로 열거나 내보내지 않는다.

journal에 jobId/projectGuid/snapshotHash/planHash/stagingGuid/attemptId/phase/batchIntent/lastVerifiedStructureHash를 저장한다. 변경 전 intent, readback 뒤 commit을 기록한다. 양쪽 원자성이 없으므로 재시작 시 실제 GUID·구조로 반영/미반영/모호함을 판정한다.

동일 job+plan 재요청은 검증된 기존 결과를 재조회한다. 사용자 편집이 섞였거나 commit 여부가 모호하면 결과를 격리·보존하고 새 attempt를 만든다. 자동 정리는 소유 GUID·예상 구조·원본 아님이 확인된 결과만 대상으로 하고 `deleteSequence()` 동작을 먼저 검증한다. 이름만으로 삭제하지 않는다. 정리 실패는 잔여물 위치를 알린다. 자동 Save As를 복구 전제로 삼지 않는다. 해당 호출은 Project 객체도 새 사본을 가리키게 한다. [Project][project]

## 8. companion·경로·데이터 계약

Windows loopback HTTP+JSON/polling을 우선한다. 127.0.0.1 전용 origin만 허용하고 외부 bind를 금지한다. 실제 UXP의 연결·timeout·취소·버전 불일치를 시험한다. 선택인 WebSocket은 UXP 클라이언트만 지원된다. [Network][network]

companion 전용 scheme과 `shell.openExternal()`을 후보로 둔다. manifest launchProcess.schemes와 UXP 사용자 동의가 필요하다. shell 성공 후 실제 handshake로 준비를 확인한다. scheme 등록·서명·배포와 독립 업데이트 관리자 실행은 [배포·업데이트 설계](distribution-and-updates.md)를 따르며 실제 설치 검증은 남아 있다. [Launch][launch]

loopback에도 인증이 필요하다. 설치 프로그램이 해당 제품의 External UXP data folder에 배치한 비공개 bootstrap으로 서버와 패널을 상호 인증한다. 서버를 검증한 뒤에만 프로젝트 정보를 보내며, 요청·응답은 세션 키와 단조 증가 카운터로 인증한다. 키·토큰·경로를 URL query/로그에 넣지 않는다. 서버는 프로토콜·크기·Host/Origin을 검사하고 CORS를 인증으로 대신하지 않는다. 부트스트랩 누락 시 수동 pairing이나 인증 생략으로 우회하지 않는다.

선택 ProjectItem 경로만 등록해 assetId로 바꾼다. Windows 정규 경로·실제 file identity·reparse target을 확인한다. 임의 경로 HTTP 서버, 외부 URL, 디바이스 경로·경로 탈출은 허용하지 않는다. UNC/placeholder는 명시 입력과 실제 읽기 가능성이 검증되어야 한다. 명령은 argv 배열로 전달하고 경로를 쉘 문자열로 조립하지 않는다.

fingerprint는 file identity·경로·크기·mtime·스트림·내용 hash다. 부분 hash만으로 동일성을 확정하지 않고 불확실하면 전체 hash를 확인한다. 분석 전후 변경은 실패, 프록시/원본은 별도 identity, 재연결은 캐시/계획 무효다.

| 메시지 | 필수 경계 |
| --- | --- |
| InputSnapshot | schemaVersion, project/sequenceRef, assetId·instanceKey, 원본 좌표, 채널 선택, 지원 상태, snapshotHash. 호스트 객체/실행코드 금지 |
| SyncPlan | referenceAsset, offset 샘플/배치 tick·원점·근거·잔여 오차·상태. 실패 소스 누락 금지 |
| EditPlan | snapshotHash, FPS 분수, 반열린 frame 구간, cameraId/sourceClipInstanceKey, source In/Out tick, 사유, planHash. 0길이·공백·불법 중첩·범위 초과·미등록 소스 거부 |
| ApplyReceipt | 버전, capability 증거, 원본/결과 GUID, 예상/실제 경계, 보존 비교, 취소·잔여물. HTTP 성공만으로 완료 금지 |

공통 schemaVersion/jobId/revision/createdAt 및 /v1 경로는 메인 계약을 따른다. assetId는 SourceAsset 키, sourceClipInstanceKey는 SourceClip.instanceKey 참조다. journal의 projectGuid/stagingGuid는 projectRef/outputSequenceRef의 호스트 GUID이며 별도 식별 체계를 만들지 않는다. HTTP companion은 Premiere에 적용하지 않고 UXP 실행기만 적용한다.

## 9. 실제 호스트 첫 승인 시험

30fps·24초·3카메라와 독립 오디오의 기준 자료에 **전환점 3개(180/360/540프레임), 구간 4개(A→B→C→A)**를 적용한다. AI는 연결하지 않는다. 오디오 볼륨·키프레임·효과, 영상 위치/크기 키프레임·색 효과, 상단 오버레이를 포함한다. 패널 로드→입력 조회→복제→적용→readback→타임라인 재생→수동 trim을 실제 Premiere에서 수행한다.

영상 경계·소스 프레임·효과·범위 밖·전체 길이·임시 클립 0개를 readback한다. 오디오 수·트랙·소스·네 경계·disabled·속도·조회 가능한 효과/키프레임은 동일해야 한다. 링크·믹서 등 API 미확정 항목은 검증 불가로 기록한다. 호스트 A/B는 화면·소리 증거이며 링크 그래프 동일성의 대체가 아니다. 읽지 못한 값은 같다고 판정하지 않고 식별 가능한 통과 구성만 허용한다. [AudioClip][aclip]

실제 빌드·원본/결과 GUID·3개 tick 경계·전환 앞뒤 재생·원본 불변·Undo 재조회를 기록한다. 반복 요청의 중복 생성, batch 취소·프로젝트 닫기·원본 변경·재시작의 거짓 완료가 없어야 한다. G04 실패는 연동 차단 사유다.

## 10. 업데이트 중 호스트 중단과 설치 인계

업데이트 버튼은 같은 설치의 모든 Contentrium CUT 작업을 중단하는 명령이다. 즉시 신규 요청을 막고 updateEpoch를 갱신한다. 각 UXP 실행기는 다음 host transaction에 들어가기 전에 유효한 실행 허가와 중단 상태를 확인한다. 기존 진행률 polling 주기나 전체 편집 완료를 기다려 중단하지 않는다.

이미 Premiere 안에 들어간 transaction은 반환을 기다리고 다음 batch를 실행하지 않는다. API 호출을 강제 절단하거나 Premiere를 kill하지 않는다. 응답이 늦으면 중단 확인 대기로 남고 파일 교체를 차단한다. batch 최대 시간은 G1/G7에서 실측해 UI가 오랫동안 멈추는 묶음을 피한다.

실행기는 batch intent·반환 결과·실제 재조회 여부·원본/출력 GUID·마지막 확인 구조·중단 사유를 ApplyReceipt/journal에 저장한다. 중단 기록에 필요한 최소 조회·저장은 허용하지만 다음 편집이나 전체 검증을 계속하지 않는다. 중단된 결과는 INTERRUPTED_OUTPUT이며, 이전 검증된 완료 결과와 원본은 보존한다. Ctrl+Z·자동 Save As·미확인 임시 시퀀스 삭제를 업데이트 준비 작업에 포함하지 않는다.

모든 참여자의 중단 확인 또는 실제 프로세스 종료가 있어야 설치로 인계한다. 첫 지원 버전에서는 별도 업데이트 관리자가 자산을 다운로드·검증한 뒤 사용자에게 프로젝트 저장과 Premiere 정상 종료를 안내한다. 패널이 닫혀도 업데이트 창과 기록은 유지한다. 동일 UXP ID의 CCX는 Adobe가 문서화한 설치 경로로 갱신하며 호스트 파일 직접 덮어쓰기는 사용하지 않는다.

Premiere 재실행 후 실제 패널 버전·Companion 버전·bundleId·프로토콜·데이터 스키마를 대조한다. 로컬 설치 메타데이터만 바뀌었거나 이전 패널이 계속 로드되었으면 업데이트 완료가 아니다. 혼합 버전은 분석·적용을 차단한다. 업데이트 완료 후에도 중단된 적용은 자동 재개하지 않고 snapshot·receipt·실제 결과를 재검사한다.

G7의 필수 호스트 시험은 적용 전/transaction 중/결과 검사 중 업데이트, 여러 패널과 중복 요청, 응답 없음, 정상 종료·재시작, 동일 ID의 CCX 교체, 일부 구성 실패와 복구다. 설치 도구 성공 코드와 실제 패널 로드·재생 검증을 구별한다.

[manifest]: https://developer.adobe.com/uxp/guides/explanation/concepts/manifest/
[project]: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/project
[projectutils]: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/projectutils
[sequence]: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequence
[vclip]: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/videocliptrackitem
[aclip]: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/audiocliptrackitem
[source]: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/clipprojectitem
[time]: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/ticktime
[editor]: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequenceeditor
[params]: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/componentparam
[locks]: https://developer.adobe.com/premiere-pro/uxp/resources/fundamentals/eslint-support/
[changes]: https://developer.adobe.com/premiere-pro/uxp/changelog/
[constants]: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/constants/
[events]: https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/eventmanager
[network]: https://developer.adobe.com/premiere-pro/uxp/resources/recipes/network/
[launch]: https://developer.adobe.com/premiere-pro/uxp/resources/recipes/external-process/
[sample]: https://github.com/AdobeDocs/uxp-premiere-pro-samples/blob/main/sample-panels/premiere-api/src/sequenceEditor.ts
