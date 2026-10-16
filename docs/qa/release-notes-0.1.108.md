## 다운로드

| 구분 | 파일 | 사용 방법 |
| --- | --- | --- |
| **온라인 설치기 · 권장** | [Contentrium-CUT-Install.exe](https://github.com/contentriumkorea/contentrium-cut/releases/download/v0.1.108/Contentrium-CUT-Install.exe) | 약 17MB. 실행 시 GitHub 최신 안정 버전을 찾아 서명·해시 검증 후 설치 창을 엽니다. 인터넷 필요 |
| **포터블 · 압축형 수동 구성** | [Contentrium-CUT-Portable-Windows-x64.zip](https://github.com/contentriumkorea/contentrium-cut/releases/download/v0.1.108/Contentrium-CUT-Portable-Windows-x64.zip) | 분석 엔진 전체와 같은 버전 CCX·한국어 안내를 압축 해제해 확인합니다. Premiere 사용에는 최초 패널 등록과 Setup 보안 연결이 필요하며, 완전 무설치 버전은 아닙니다. |
| 수동 설치·복구 | [Contentrium-CUT-Setup.exe](https://github.com/contentriumkorea/contentrium-cut/releases/download/v0.1.108/Contentrium-CUT-Setup.exe) | 버전별 전체 설치 엔진. 기존 자동 업데이트와 복구 경로 유지 |

항상 최신 설치기를 받는 주소: [온라인 설치기 다운로드](https://github.com/contentriumkorea/contentrium-cut/releases/latest/download/Contentrium-CUT-Install.exe).

## 바뀐 점

- 최초 다운로드를 작은 온라인 설치기와 버전별 본체로 분리했습니다. 같은 온라인 설치 파일도 실행 시점의 최신 안정 버전을 찾습니다.
- 선택한 Release ID·버전·서명 문서를 고정한 뒤 전체 Setup을 다운로드하고 SHA-256을 검증합니다. 설치 도중 새 버전이 게시돼도 다른 버전으로 바꾸지 않습니다.
- 취소·서명 오류·크기/해시 불일치·철회된 배포는 설치 파일을 실행하지 않습니다.
- GitHub 다운로드 안내에서 온라인 설치기, 포터블 압축형, 수동 설치 구성요소를 구분했습니다. 추가 파일은 별도의 `distribution-manifest.json/.sig`로 서명합니다.

## 설치와 업데이트

온라인 설치기의 **최신 버전 다운로드 및 설치**를 누른 뒤 새로 열린 설치 창의 안내를 따릅니다. Premiere 프로젝트를 저장하고 정상 종료한 상태에서 패널을 등록하며, 이후 Premiere에서 Contentrium CUT 패널을 직접 열어 첫 연결을 마칩니다. 기존 설치는 패널의 **업데이트**를 사용하세요. 기존 사용자 설정·모델·자료를 자동 삭제하지 않습니다.

기존 `Contentrium-CUT-Windows-x64.zip`, `Contentrium-CUT.ccx`, `update-manifest.json/.sig`는 자동 업데이트·복구 호환용입니다. 원래 0.1.0의 고정 v0.1.1 전환 파일은 유지합니다. 배포 정보 서명은 Ed25519이며 Windows Authenticode 서명과는 별개입니다.

Windows x64 / Premiere Pro 26.5.2 대상입니다. 실제 사용자 PC 재설치·Premiere 편집·첫 패널 연결·Community-1 실제 품질은 이번 CLI/패키지 검증에 포함하지 않습니다. 원음·프로젝트·화자 정보는 GitHub에 업로드하지 않습니다.
