# 설치와 사용 안내

[사용자용 첫 화면](../README.md)

## 다운로드와 설치

| 구분 | 다운로드 | 용도 |
| --- | --- | --- |
| **온라인 설치기 · 권장** | [Contentrium-CUT-Install.exe](https://github.com/contentriumkorea/contentrium-cut/releases/latest/download/Contentrium-CUT-Install.exe) | 작은 파일을 실행하면 GitHub 최신 안정 버전의 서명·해시를 검증하고 설치 창을 엽니다. 인터넷 필요 |
| **포터블 · 압축형 수동 구성** | [Windows x64 ZIP](https://github.com/contentriumkorea/contentrium-cut/releases/latest/download/Contentrium-CUT-Portable-Windows-x64.zip) | 분석 엔진 전체·같은 버전 CCX·한국어 안내. Premiere 사용에는 최초 패널 등록과 Setup의 보안 연결이 필요합니다. 완전 무설치 버전은 아닙니다. |
| 수동 설치·복구 구성요소 | [전체 Setup](https://github.com/contentriumkorea/contentrium-cut/releases/latest/download/Contentrium-CUT-Setup.exe) / [원본 엔진 ZIP](https://github.com/contentriumkorea/contentrium-cut/releases/latest/download/Contentrium-CUT-Windows-x64.zip) / [패널 CCX](https://github.com/contentriumkorea/contentrium-cut/releases/latest/download/Contentrium-CUT.ccx) | 기존 설치·자동 업데이트와 복구 도구의 호환 경로 |

1. **온라인 설치기**를 내려받아 실행하고 **다운로드 및 설치**를 누릅니다. 처음 받은 설치기도 실행 시점의 최신 안정 버전을 찾습니다.
2. 검증된 전체 설치 창이 열리면 프로젝트를 저장하고 Premiere를 정상 종료한 뒤 설치합니다. 선택 후 최신 버전이 바뀌어도 같은 버전으로 설치를 이어갑니다.
3. 패널 등록 후 Premiere에서 Contentrium CUT을 직접 열어 첫 연결을 마칩니다. 이미 설치되어 있다면 기존 패널의 **업데이트**로 갱신합니다. 설치기를 실행했다고 기존 설치가 자동으로 삭제되지는 않습니다.

각 공개 버전은 `Contentrium CUT`이라는 제목으로 게시합니다. 버전별 원본 자산을 교체하지 않습니다. 기존 업데이트용 `update-manifest.json/.sig`와 추가 배포용 `distribution-manifest.json/.sig`의 Ed25519 서명 및 SHA-256으로 파일을 검증합니다. Windows Authenticode 서명과는 별개입니다.

**[0.1.109 공개](https://github.com/contentriumkorea/contentrium-cut/releases/tag/v0.1.109).** [패키지·공개 다운로드 검증](qa/distribution-0.1.109-public-proof.json)을 완료했습니다.

**0.1.109 변경:** 설치 창에 실제 다운로드 진행률·용량·속도를 추가하고, 전체 Setup 오류 코드와 해결 안내를 온라인 설치기에 전달합니다. 기본 깃털 아이콘을 교체했습니다. [설치 개선 설계](superpowers/specs/2026-10-17-installer-feedback-design.md), [실행·검증 기록](qa/installer-feedback-0.1.109.md).

설치가 실패하면 **진단 내용 복사**로 오류 코드와 내용을 확인하세요. Premiere 종료 후에도 실패한다면 일반 인계 안내만으로 원인을 단정할 수 없습니다. 기존 폴더·자료·설정을 임의로 삭제하지 마세요. 실제 사용자 PC 설치·설치 화면 시각 검수·Premiere 첫 연결/편집·Community-1 실제 품질은 CLI/패키지 검증과 별개입니다.

## 단일 패널 사용 흐름

설치 파일은 [콘텐츠리움 GitHub](https://github.com/contentriumkorea/contentrium-cut/releases)의 `Contentrium-CUT-Install.exe`를 권장합니다. 설치할 때 Premiere를 정상 종료하며, 설치기는 서명된 배포 정보와 파일 해시를 검증합니다.

1. Premiere의 **Window → UXP Plugins → Contentrium CUT** 패널을 엽니다. 패널이 현재 시퀀스와 백그라운드 엔진을 자동으로 확인합니다.
2. **트랙**에서 실제 V/A 트랙의 카메라 역할, 분석할 마이크, 오디오 스트림·채널과 적용 범위를 지정합니다. 필요한 경우 자동·수동·타임코드 싱크를 설정합니다.
3. **화자**에서 화자별 마이크 또는 혼합 녹음을 분석합니다. 예시를 듣고 이름·카메라를 연결하며, 겹침이나 불확실한 화자는 직접 교정합니다.
4. **컷 편집**에서 최소 샷 길이, 짧은 발화, 동시 발화와 수동 고정 구간을 설정합니다.
5. **검토**에서 컷을 50개씩 이동하며 카메라·선택 이유·프레임 범위와 길이로 찾아볼 수 있습니다. 원본 프레임은 0부터 시작하고 종료는 다음 구간의 시작 경계이며, 표시 시간은 경과 시간입니다. 필터는 표시만 바꾸고 적용할 전체 편집안은 유지합니다. 원본을 보존한 결과 시퀀스가 생성됩니다.

영상 결과는 모든 영상 트랙 위의 **Contentrium CUT** 트랙에 개별 컷으로 정리합니다. 기존 자막 트랙과 오디오·그래픽 데이터는 보존합니다. 로고 배치와 추가 그래픽 편집은 사용자가 직접 합니다.

모델, 자원·캐시, 업데이트와 중단된 편집 복구도 패널의 설정에서 제어합니다.

하단 실행 버튼 위의 안내는 단계 이동과 작업 상태 변화에 맞춰 갱신됩니다. 동봉된 발화 모델이 없으면 Setup 복구를, 혼합 녹음 모델이 없으면 설정에서 준비하도록 안내합니다. 업데이트와 중단 작업 복구 안내는 일반 단계 안내보다 먼저 표시합니다. 이 안내는 실제 모델·호스트 검증을 대신하지 않습니다.

현재 실제 호스트 검증 대상은 **Windows / Premiere Pro 26.5.2**입니다. 다른 호스트 버전은 검증 없이 편집을 허용하지 않습니다.

## 모델과 개인정보

화자별 마이크 모드는 포함된 Silero VAD를 사용합니다. 혼합 녹음의 Community-1 모델은 제공자의 계정 접근 권한과 이용 조건 동의가 필요합니다. 패널 설정에서 Hugging Face 토큰과 고정 리비전으로 설치하며 토큰을 저장하지 않습니다. 모델 설치 후 오디오 분석은 로컬에서 수행합니다. 원음·프로젝트·화자 정보는 GitHub에 전송하지 않습니다.

FFmpeg는 설치 중 제공자의 고정 버전 패키지를 사용자의 PC에 직접 내려받습니다. 콘텐츠리움의 배포 파일에는 FFmpeg 바이너리를 재배포하지 않습니다. 다운로드 출처·버전·해시·공급자 라이선스는 로컬에 보관합니다.

## 업데이트

패널을 열면 같은 저장소의 게시된 업데이트를 확인합니다. 새 버전이 없으면 편집을 계속하고, 있으면 패널 안에 업데이트 알림을 표시합니다. **업데이트**를 누르면 즉시 새 CUT 작업을 차단하고 분석을 취소합니다. 이미 실행 중인 Premiere 편집 호출은 반환 경계에서 멈춥니다. 다운로드와 검증 이후 Premiere 정상 종료를 기다려 Adobe 설치기로 패널을 교체합니다. 새 패널과 백그라운드 엔진의 버전·연결이 확인되어야 작업 차단이 해제됩니다. 중단된 편집은 자동 재개하지 않습니다.

업데이트 정보는 Ed25519로 서명합니다. Windows 실행 파일의 Authenticode 서명과는 별개입니다.

GitHub 확인은 백그라운드에서 처리하며 이미 검증된 후보의 업데이트 시작을 막지 않습니다. 미리보기 종료가 느려도 서버 중단 요청을 먼저 보냅니다. 응답이 유실되면 편집을 재개하지 않고 같은 요청 ID로 재시도합니다.

원래 공개 0.1.0의 설치 검증 오류와 새 실행 구조 사이 전환은 [소스 보조 0.1.0 → 0.1.1 전환 절차](source-assisted-migration.md)를 사용합니다. 전용 headless 도구의 소스 검증과 실제 설치 성공은 구분하며, 기존 실패 기록을 보존합니다.

