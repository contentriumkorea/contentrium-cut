## 다운로드

| 구분 | 파일 | 사용 방법 |
| --- | --- | --- |
| **온라인 설치기 · 권장** | [Contentrium-CUT-Install.exe](https://github.com/contentriumkorea/contentrium-cut/releases/download/v0.1.109/Contentrium-CUT-Install.exe) | 약 17MB. 최신 안정 버전을 찾아 서명·해시 검증 후 전체 설치 창을 엽니다. 인터넷 필요 |
| **포터블 · 압축형 수동 구성** | [Contentrium-CUT-Portable-Windows-x64.zip](https://github.com/contentriumkorea/contentrium-cut/releases/download/v0.1.109/Contentrium-CUT-Portable-Windows-x64.zip) | 본체·같은 버전 CCX·한국어 안내. Premiere 사용에는 최초 패널 등록과 Setup 보안 연결이 필요합니다. 완전 무설치 버전은 아닙니다. |
| 수동 설치·복구 | [Contentrium-CUT-Setup.exe](https://github.com/contentriumkorea/contentrium-cut/releases/download/v0.1.109/Contentrium-CUT-Setup.exe) | 전체 설치 엔진. 기존 업데이트·복구 호환 유지 |

항상 최신 파일: [온라인 설치기 다운로드](https://github.com/contentriumkorea/contentrium-cut/releases/latest/download/Contentrium-CUT-Install.exe).

## 바뀐 점

- 온라인 설치기와 전체 설치 창을 같은 모노 디자인으로 정리하고, 기본 깃털 아이콘을 단색 C 아이콘으로 바꿨습니다.
- 다운로드 진행률·받은 용량/전체 용량·속도·예상 남은 시간을 실제 전송량으로 표시합니다. 검증·Adobe 등록·패널 연결처럼 수치 측정이 안 되는 단계는 처리 중으로 표시합니다.
- 기존 설치 위치와 파일을 확인하는 동안에도 먼저 진행 화면을 표시합니다. 확인 작업은 백그라운드에서 수행하며, 취소를 요청하면 현재 검증 경계에서 안전하게 중단합니다.
- 전체 설치 창의 실패 이유가 일반 안내문으로 가려지던 문제를 수정했습니다. 오류 코드·해결 안내·종료 코드를 표시하고 **진단 내용 복사**와 재시도를 제공합니다.
- 전체 설치 시작 전 오류도 기록하며, 온라인 설치기에서 별도 Setup을 실행할 때 부모의 임시 Python/Tcl 경로를 넘기지 않습니다.
- 실제 설치 없이 시작 환경과 설치 위치를 확인하는 진단 경로를 추가했습니다. 실행 직전 배포 재확인 중에도 취소를 반영합니다.

## 설치가 계속 안 될 때

기존 온라인 설치 파일 대신 위의 새 온라인 설치기를 받으세요. 실패하면 **진단 내용 복사**로 표시된 오류 코드와 내용을 전달해 주세요. `검증된 설치 창에서 계속해 주세요`는 설치 창으로 이어지는 안내이며, 그것만으로 Premiere 실행이 실패 원인이라고 판단할 수 없습니다. 기존 설치 폴더·자료·설정은 임의로 삭제하지 마세요.

설치 파일 준비 후에는 Premiere에서 **Window → UXP Plugins → Contentrium CUT** 패널을 직접 열어 첫 연결을 마칩니다. 기존 설치는 패널의 업데이트를 사용합니다. 파일 준비와 실제 패널 연결 완료를 구별합니다.

기존 자동 업데이트용 3역할·Ed25519 신뢰 키·고정 v0.1.1 전환 자산을 보존합니다. 배포 정보 서명은 Windows Authenticode와 별개입니다.

Windows x64 / Premiere Pro 26.5.2 대상입니다. CLI·패키지 검증과 별개로, 보고된 다른 PC의 설치 성공·실제 설치 화면의 시각 검수·Premiere 첫 연결/편집·Community-1 실제 품질은 미검증입니다. 사용자 PC 재설치나 Premiere 조작은 수행하지 않았습니다.
