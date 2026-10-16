# Contentrium CUT 온라인 설치·배포 설계

사용자 요청: 작은 설치 파일을 GitHub에서 쉽게 받고, 실행 시 최신 안정 버전을 찾아 다운로드·설치한다. GitHub에서 설치 파일과 포터블을 구분한다. 설계부터 공개 배포까지 이번 요청으로 진행한다. 반복 자동화는 PAUSED이며 사용자 PC 재설치·Premiere 조작은 포함하지 않는다.

## 선택

1. 기존 93MB Setup만 축소: 설치·복구·모델 검증 의존성이 연결되어 크기와 호환성이 함께 변한다.
2. **권장·채택: 작은 온라인 진입점 + 기존 서명 검증 설치 엔진.** 최초 다운로드에서 분석 의존성을 제외하고, 최신 release를 한 번 선택한 뒤 정확한 tag의 검증된 Setup에 넘긴다. 기존 업데이트·복구 계약을 보존한다.
3. 새 네이티브 설치 엔진: 용량 이점은 있지만 Adobe 등록·복구를 중복 구현하므로 이번 범위에서 제외한다.

## 파일과 신뢰

- `Contentrium-CUT-Install.exe`: 추천 온라인 설치기, 20MiB 이하를 배포 관문으로 한다. 버전별 payload를 포함하지 않으며 신뢰 키·제품 ID·공식 저장소만 고정한다. 저장해 둔 같은 설치기로 이후 최신 버전을 찾는다.
- `Contentrium-CUT-Portable-Windows-x64.zip`: 압축형 수동 구성. 기존 분석 엔진 전체, 같은 버전 CCX, 한국어 안내를 포함한다. Premiere 플러그인이므로 완전 무등록·다른 PC로 설정까지 이동하는 포터블이라고 주장하지 않는다. 최초 패널 등록·보안 연결은 Setup이 필요하다.
- 기존 `Contentrium-CUT-Setup.exe`, `Contentrium-CUT-Windows-x64.zip`, `Contentrium-CUT.ccx`, `update-manifest.json/.sig`는 호환용으로 유지한다. 업데이트 manifest 역할은 기존 3개 그대로 유지하여 0.1.0 및 고정 v0.1.1 전환을 깨지 않는다.
- 새 `distribution-manifest.json/.sig`가 온라인 설치기와 압축형 파일의 실제 GitHub assetId·크기·SHA-256을 Ed25519로 보증한다. update manifest와 같은 기존 신뢰 키를 사용한다. 공개 후 파일 교체 금지.
- 추천 URL은 `/releases/latest/download/Contentrium-CUT-Install.exe`. 제목은 `Contentrium CUT`. README/공개 설명 첫 부분에 다운로드 표와 용도·필요 조건을 표시한다.

## 온라인 흐름

최신 안정 Release → 기존 update manifest 정확한 바이트 서명 검증 → 제품/채널/버전/플랫폼/assetId/크기/해시 검증 → installer 역할 Setup 스트리밍 다운로드 → 크기·SHA-256 재검증 → 선택한 release ID와 문서 재확인 → `Setup.exe --tag v선택버전` 실행. 실행 후 latest가 바뀌어도 선택 버전을 바꾸지 않는다. 기존 설치는 기존 패널 업데이트 경로를 이용하며 기존 설치를 덮어쓰지 않는다.

GUI는 한국어 조회·다운로드 상태와 취소를 제공하고 네트워크 작업은 worker에서 수행한다. CLI `--check --result`는 설치 없이 서명과 최신 발견을 검사한다. `--download-only --destination`은 실행 없이 실제 Setup 다운로드를 검사한다. 실패·취소·미확인 네이티브 활성화를 설치 완료로 표시하지 않는다. 공개 조회에는 PAT가 필요 없다.

## 수용·검증

- 변조 서명/해시, prerelease/draft, 다른 제품·플랫폼, 중복 asset, 최신 변경, 메타데이터 철회, 취소, 기존 파일 덮어쓰기 차단을 회귀 검증한다.
- 온라인 설치기 패키지에서 AI/과학 연산 모듈을 제외하고 크기 관문과 실제 frozen CLI 실행을 확인한다.
- Python·Node 전체 회귀, 독립 리뷰, runtime probe/CCX 비교/Setup help, 공개 익명 최신 발견·두 manifest 서명·모든 실제 다운로드 SHA 검증, 고정 v0.1.1 보존을 확인한다.
- 실제 사용자 PC 설치와 Premiere 편집·첫 패널 연결·Community-1 실제 품질은 수행하지 않았다고 명시한다.
