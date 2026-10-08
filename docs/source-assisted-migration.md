# 소스 보조 0.1.0 → 0.1.1 전환

이 문서는 검토된 소스의 `tools.maintenance_entry` 전용 절차입니다. 공개된 원래 0.1.0 실행 파일의 Adobe 교차 DB 검증 오류는 그 바이너리에 남아 있습니다. 이 전환은 **소스 보조 전환**이며 원래 업데이트 프로그램의 성공으로 기록하지 않습니다. 현재 단계에서는 소스와 임시 자료 시험만 수행했습니다. 실제 PC에서의 전환은 독립 소스 검토와 이후 허용된 native 설치 단계에서만 수행합니다. 0.1.2는 이 도구의 대상이 아닙니다.

이전의 소스 Companion GUI 실행·연결 코드·pairing 절차 대신 아래 headless 진입점을 사용합니다. 단일 패널의 일반 사용과 일상 업데이트에서는 이 명령이 필요하지 않습니다.

## 실행 명령

2026-10-06 공개 대상은 Release `404226839`, tag `v0.1.1`, commit `268d27e6665c8b1f0753a634f8021dfa86935b37`이다. 공개 서명·자산 재다운로드는 검증했으며 실제 로컬 전환은 아직 수행하지 않았다. 실행 직전에도 아래 절차의 동일한 대상 검증이 필요하다.

저장소 루트의 기존 Python 3.12 환경과 `requirements.lock` 의존성을 사용합니다. 설치 루트는 기존 Launcher가 속한 **절대 물리 경로**를 확인해 입력합니다. 환경 변수로 추정한 두 번째 설치 경로를 만들지 않습니다. 커밋은 검토된 공개 `v0.1.1` Release의 `target_commitish`와 같은 소문자 40자리 SHA입니다. 아직 실제 0.1.1 Release가 없으면 값을 만들어 넣지 않습니다.

```powershell
$env:PYTHONPATH = '.;companion'
$env:PYTHONDONTWRITEBYTECODE = '1'
$migrationRoot = Read-Host '기존 Contentrium CUT 설치의 절대 물리 경로'
$migrationCommit = Read-Host '검토된 v0.1.1 Release의 40자리 커밋 SHA'
.build-venv/Scripts/python.exe -m tools.maintenance_entry --root $migrationRoot --tag v0.1.1 --version 0.1.1 --commit $migrationCommit --action start --wait
```

대체 공개 키, activation token 또는 완료 영수증을 CLI로 전달하는 옵션은 없습니다. 신뢰 기준은 해당 설치에 저장된 공개 키와 원래 서명된 배포 기록입니다. 전용 전환 도구는 `v0.1.1` tag를 직접 조회하므로 이후 일반 버전이 공개되어도 이 경로를 사용할 수 있습니다. 최신 버전 조회의 ETag는 재사용하지 않습니다. 조회 결과의 tag·version·정확한 commit·서명이 요청과 다르면 시작을 거부합니다. 선택한 Release ID·tag·version·commit·서명 자산을 journal에 고정하고 교체 직전에 같은 Release ID를 다시 확인합니다. 일반 패널 업데이트는 계속 `latest`를 사용합니다.

## 단계와 중단

1. 기존 감독 프로세스와 런타임/Setup의 lease를 확보합니다. `UPDATER_ALREADY_RUNNING`이면 소유자를 강제 종료하지 않고 진단 상태로 멈춥니다. Premiere 프로젝트를 저장하고 Premiere 및 기존 CUT 프로세스를 정상 종료하는 일은 사용자의 native 선행 작업입니다.
2. 원래 0.1.0의 서명된 배포·파일·Adobe 등록과 Launcher 소유권을 검증합니다. 기존 updater가 작업 gate를 닫고 서명 자산을 내려받습니다. `WAITING_HOST_EXIT`에서는 Premiere의 실제 종료를 기다립니다.
3. 기존 updater의 snapshot·Adobe 교체·서명된 안정 Launcher 교체를 수행합니다. updater의 `PENDING_ACTIVATION`은 아직 설치 완료가 아닙니다.
4. `OPEN_PREMIERE_PANEL` 안내에서 Premiere의 Contentrium CUT 패널을 한 번 엽니다. 그 패널의 자기 data folder 영수증을 기다린 뒤 private bootstrap, 포함 Silero 모델, 기존 사용자 설정을 보존하는 시작 등록을 준비합니다. disabled 시작 항목은 재활성화하지 않습니다.
5. 준비와 등록 완료 뒤에만 300초 유효한 일회용 ticket을 만들고, 소스 소유권을 모두 해제한 후 검증된 새 감독 프로세스를 한 번 시작합니다. ticket은 상속 pipe로만 전달합니다. `HANDOFF_DISPATCHED`도 `COMPLETE`가 아닙니다. 실제 새 frozen 런타임과 설치된 패널의 연결 증명이 updater의 `COMPLETE`를 기록해야 합니다.

`--wait`를 생략하면 대기 상태를 즉시 출력하고 종료합니다. Ctrl+C 또는 대기 중 중단은 journal과 준비 기록을 남깁니다. 연결 준비 중이었다면 **같은 root/tag/version/commit**으로 다음 명령을 실행합니다. Premiere를 패널 확인용으로 다시 열어도 이 재개는 CCX를 다시 교체하지 않습니다.

```powershell
.build-venv/Scripts/python.exe -m tools.maintenance_entry --root $migrationRoot --tag v0.1.1 --version 0.1.1 --commit $migrationCommit --action resume --wait
```

`resume`은 정확한 `PENDING_ACTIVATION`, 미발급 ticket, 취소되지 않은 시도와 검증된 자산·snapshot·현재 설치에만 허용됩니다. 준비를 마친 기존 private bootstrap은 유지합니다. 이미 발급된 ticket은 만료·소비·전달 실패 여부와 관계없이 재발급하지 않습니다.

다운로드·호스트 종료 대기·교체 중 끊겼거나 이미 ticket이 발급됐다면 기존 소유자를 정상 종료하고 아래 **명시적 복구**를 사용합니다. 교체 뒤 복구는 Premiere를 다시 정상 종료해야 하는 기존 rollback 경로입니다. 도구가 `ROLLED_BACK` 또는 이전 설치가 검증된 `FAILED_BEFORE_REPLACE`를 반환한 것을 확인한 다음에만, 별도 `--action start`로 동일 대상을 새 시도합니다. 복구가 `RECOVERY_REQUIRED`로 남으면 자동 재설치하지 않습니다.

```powershell
.build-venv/Scripts/python.exe -m tools.maintenance_entry --root $migrationRoot --tag v0.1.1 --version 0.1.1 --commit $migrationCommit --action recover
```

종료 코드 0은 handoff dispatch, 2는 결과 확인이 필요한 대기/복구 상태, 1은 거부/실패, 130은 사용자 중단입니다. `COMPLETE`는 CLI 종료 코드로 판단하지 않습니다.

## 보존과 검증 범위

`updates/journal.json`이 유일한 updater 상태 기준입니다. 소스는 변경 전 journal의 정확한 바이트를 `updates/maintenance-history/<SHA-256>/journal.json`에 추가 보관하며, 같은 해시에 충돌하는 기존 기록이 있으면 거부합니다. `first-install.json`, 원래 공개 payload, 실패 증거, 사용자 작업과 모델은 삭제하거나 초기화하지 않습니다. 소스 보조 방식과 정확한 대상·시도 ID는 updater journal의 비밀값 없는 부가 기록으로 남깁니다.

등록 변경 전에는 그 updater 시도와 snapshot에 묶인 `updates/migration-registration/<snapshot hash>/migration-registration.json`에 정확한 이전·예정 protocol 및 이름 지정 Run 값을 보관합니다. 소스의 명시적 복구와 새 런타임의 활성화 실패 rollback 모두 이 기록을 사용합니다. 현재 값이 해당 시도의 값과 같을 때만 기존 legacy protocol을 복원하고, 그 시도가 새로 만든 정확한 `Run/Contentrium CUT` 값만 제거합니다. 기존 Run 값, 다른 시작 항목과 StartupApproved는 보존합니다. 사용자가 바꾼 값이나 다른 시도의 기록은 덮어쓰지 않으며 충돌은 `RECOVERY_REQUIRED`로 남습니다. 복원 도중 끊겨도 같은 기록으로 재시도합니다.

소스 시험은 서명·중단 복구·준비 재개·등록 순서·단일 pipe dispatch를 임시 파일과 주입된 OS/Adobe/registry/process 경계에서 검증합니다. 실제 UXP 폴더, DACL, Adobe 등록, frozen 프로세스, 상속 pipe와 native activation 성공은 후속 실제 설치 검증 대상입니다.
