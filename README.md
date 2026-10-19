<p align="center">
  <img src="docs/assets/cover.svg" alt="Contentrium CUT — 말하는 화자를 따라 이어지는 편집 가능한 컷" width="100%">
</p>

<h1 align="center">Contentrium CUT</h1>

<p align="center">
  <strong>말하는 화자를 따라, Premiere에서 계속 다듬는 컷.</strong><br>
  인터뷰·대담 편집을 위해 트랙 설정, 싱크, 화자 분석과 컷 검토를 하나의 패널에 모았습니다.
</p>

<p align="center">
  <a href="https://github.com/contentriumkorea/contentrium-cut/releases/latest"><img src="docs/assets/badge-version.svg" alt="현재 정식 버전 0.1.110" height="28"></a>
  <a href="docs/installation.md"><img src="docs/assets/badge-windows.svg" alt="대상 환경 Windows x64" height="28"></a>
  <a href="package.json"><img src="docs/assets/badge-license.svg" alt="프로젝트 라이선스 표기 UNLICENSED" height="28"></a>
</p>

<p align="center">
  <a href="#빠르게-시작하기">설치하기</a> ·
  <a href="https://github.com/contentriumkorea/contentrium-cut/releases/latest/download/Contentrium-CUT-Install.exe">다운로드</a> ·
  <a href="#사용-모습">사용 모습</a> ·
  <a href="docs/installation.md">상세 안내</a>
</p>

---

## 빠르게 시작하기

**Windows x64 · Premiere Pro 26.5.2 대상.** 처음 사용하는 PC에서는 아래 온라인 설치기를 실행하세요. 약 17MB의 진입 파일이 최신 정식 버전을 찾아 내려받고 검증합니다.

<p align="center">
  <a href="https://github.com/contentriumkorea/contentrium-cut/releases/latest/download/Contentrium-CUT-Install.exe"><img src="docs/assets/download-install.svg" alt="온라인 설치기 다운로드 — 처음 설치할 때 권장" width="284" height="52"></a>
  <a href="https://github.com/contentriumkorea/contentrium-cut/releases/latest/download/Contentrium-CUT-Portable-Windows-x64.zip"><img src="docs/assets/download-manual.svg" alt="압축형 수동 구성 다운로드 — 최초 패널 등록과 보안 연결 필요" width="260" height="52"></a>
</p>

1. 온라인 설치기에서 **다운로드 및 설치**를 누릅니다.
2. 전체 설치 창의 안내에 따라 프로젝트를 저장하고 Premiere를 정상 종료한 뒤 설치합니다.
3. Premiere의 **Window → UXP Plugins → Contentrium CUT**을 열어 첫 연결을 마칩니다.

**0.1.110 변경:** 05 Mono Studio 기준으로 소스·싱크·화자·편집 설정을 한 작업 화면으로 정리했습니다. 상세 옵션은 고급 설정으로 접고, 분석 결과에 따라 주 버튼이 바뀌며 편집안 생성 후 검토 영역으로 이동합니다.

이미 설치했다면 패널의 **업데이트**를 사용하세요. 압축형 구성도 최초 패널 등록과 보안 연결이 필요한 **Premiere 플러그인**이며, 완전 무설치 포터블 앱은 아닙니다.

[상세 설치 및 업데이트 안내 →](docs/installation.md)

## 화자에서 컷까지, 하나의 패널

| 01 · 트랙과 싱크 | 02 · 화자와 카메라 | 03 · 수정 가능한 개별 컷 |
| :--- | :--- | :--- |
| **입력과 시간축을 정리** | **말하는 사람에 맞춰 검토** | **Premiere에서 계속 편집** |
| 카메라·마이크·채널·사용 범위를 지정하고 자동·수동·타임코드 싱크를 설정합니다. | 화자별 마이크 또는 혼합 녹음을 로컬로 분석하고 화자와 카메라를 연결합니다. | 새 결과 시퀀스의 최상단 **Contentrium CUT** 트랙에 개별 컷을 만들고 원본·오디오·별도 자막을 보존합니다. |

## 사용 모습

<p align="center">
  <img src="docs/assets/workflow.svg" alt="트랙과 화자 연결 → 싱크·분석·검토 → 원본을 보존한 개별 컷 생성의 기능 흐름도. 실제 제품 화면이 아닙니다." width="880">
</p>

**말하는 화자를 연결하고, 검토한 편집안을 개별 컷으로 만듭니다.**

위 이미지는 실제 기능을 설명하는 **흐름도이며 제품 화면이 아닙니다.** 자동 분석에서 겹치거나 불확실한 구간은 사용자가 검토·교정합니다.

<details>
<summary><strong>추가 예시 보기</strong></summary>

**화자별 마이크가 있는 대담**

트랙별 카메라·마이크를 지정 → 싱크 확인 → 화자·카메라 연결 → 컷 길이와 겹침 구간 검토 → 새 결과 시퀀스에 적용합니다.

**한 녹음에 여러 화자가 섞인 인터뷰**

권한과 이용 조건을 확인해 혼합 녹음 모델을 준비 → 화자 분석 → 이름·카메라 연결과 불확실 구간 교정 → 편집안 검토 후 적용합니다. Community-1의 실제 화자 구분 품질은 아직 미검증입니다.

</details>

## 플러그인 설치·배포 안내

| 제공 형태 | 사용 방법 |
| :--- | :--- |
| [온라인 설치기 · 권장](https://github.com/contentriumkorea/contentrium-cut/releases/latest/download/Contentrium-CUT-Install.exe) | 실행 시 최신 정식 버전을 확인하고 필요한 파일을 내려받아 전체 설치 창으로 이어집니다. 인터넷이 필요합니다. |
| [압축형 수동 구성](https://github.com/contentriumkorea/contentrium-cut/releases/latest/download/Contentrium-CUT-Portable-Windows-x64.zip) | 분석 엔진·같은 버전의 패널·한국어 안내를 포함합니다. 파일 복사만으로 최초 등록과 보안 연결을 대신할 수 없습니다. |
| [패널 CCX](https://github.com/contentriumkorea/contentrium-cut/releases/latest/download/Contentrium-CUT.ccx) · [전체 Setup](https://github.com/contentriumkorea/contentrium-cut/releases/latest/download/Contentrium-CUT-Setup.exe) | 수동 등록·설치·복구용 구성요소입니다. 기존 자료·설정·복구 기록을 보존하고 [설치 안내](docs/installation.md)를 따릅니다. |

기존 설치는 패널에서 업데이트합니다. 업데이트 클릭 즉시 새 작업을 차단하고 중단을 요청하며, 실제 작업이 끝나고 Premiere가 정상 종료된 뒤 파일을 교체합니다. 중단된 편집은 자동으로 재개하지 않습니다.

## 이렇게 사용하세요

```text
트랙       카메라·마이크·채널·사용 범위를 연결하고 싱크 확인
화자       이름과 카메라를 연결하고 불확실한 분석 구간 교정
컷 편집    최소 샷 길이·동시 발화·수동 고정 구간 설정
검토       편집안을 확인한 뒤 새 결과 시퀀스에 적용
Premiere   최상단 Contentrium CUT 트랙의 개별 컷을 계속 수정
```

로고 배치와 추가 그래픽 편집은 사용자가 직접 합니다.

## 적용 범위와 제한

- **대상 환경:** Windows x64 / Premiere Pro 26.5.2. 검증하지 않은 호스트 버전이나 미디어 구성의 편집 적용은 차단합니다.
- **모델·개인정보:** 혼합 녹음 모델은 제공자의 계정 접근 권한과 이용 조건 동의가 필요하며 별도로 준비합니다. 준비한 모델로 로컬 분석을 수행하고 원음·프로젝트·화자 정보를 GitHub에 보내지 않습니다.
- **미검증 범위:** 실제 다른 PC 설치 성공, Premiere 첫 연결·편집, Community-1의 실제 화자 구분 품질은 아직 미검증입니다. 다운로드·패키지 검증과 구별합니다.
- **복구와 이전 버전:** 기존 자료·설정·복구 기록을 임의로 삭제하지 마세요. 기존 업데이트와 고정 전환 주소를 보호하기 위해 이전 릴리스를 공개로 보존합니다.

<details>
<summary><strong>상세 안내와 검증 범위</strong></summary>

- **0.1.109 변경:** 설치 진행률·용량·속도·남은 시간, 오류 코드·진단 복사·재시도와 설치 아이콘을 개선했습니다. [변경 사항](docs/releases/0.1.109.md)
- [자세한 설치·실패 진단·모델·지원 범위](docs/installation.md)
- [패키지·공개 다운로드 검증](docs/qa/distribution-0.1.109-public-proof.json) · [설치 개선 검증](docs/qa/installer-feedback-0.1.109.md)
- [설계·개발·검증 자료](docs/development.md) · [기존 0.1.0 → 0.1.1 전환 안내](docs/source-assisted-migration.md)
- [제3자 라이선스·귀속 자료](licenses/)와 패널의 `vendor/` 고지를 유지합니다.

</details>

<details>
<summary><strong>English overview</strong></summary>

Contentrium CUT is a Premiere Pro plugin for speaker-led camera cuts. Configure tracks and sync, analyze local audio, review speaker-to-camera assignments, and create editable cuts on a new result sequence. Original media, audio, and separate captions are preserved.

The online installer downloads and verifies the latest stable package. The manual ZIP still requires initial panel registration and secure pairing; it is not a registration-free portable app. Target environment: Windows x64 / Premiere Pro 26.5.2. Actual installation on other PCs, native Premiere connection/editing, and Community-1 speaker quality remain unverified.

</details>

---

<p align="center">
  <strong>콘텐츠리움 · CONTENTRIUM</strong><br>
  <a href="package.json">UNLICENSED</a> ·
  <a href="licenses/">제3자 라이선스 고지</a> ·
  <a href="https://github.com/contentriumkorea/contentrium-cut/releases/latest">Latest release</a>
</p>

<sub>Contentrium CUT은 콘텐츠리움의 Premiere Pro용 플러그인입니다. 타사 구성요소의 저작권·라이선스는 각 고지를 따릅니다.</sub>

<p align="center">
  <a href="https://contentrium.com/"><img src="docs/assets/contentrium-home.svg" alt="콘텐츠리움 홈페이지 바로가기" width="260" height="52"></a>
</p>
