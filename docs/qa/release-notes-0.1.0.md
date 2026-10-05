Contentrium CUT의 첫 공개 시험판입니다.

- Premiere Pro 26.5.2 현재 시퀀스에서 화자 분석, 카메라 연결, 편집안 검토와 별도 시퀀스 컷 적용.
- 공통 오디오 자동 싱크와 영상·오디오를 보존한 싱크 복제본 생성.
- 분리 마이크 Silero 로컬 분석. 혼합 녹음 Community-1은 제공자 계정의 접근 승인과 이용 조건 동의 후 모델 설치 필요.
- Mono Studio 패널과 Windows Companion, 서명된 GitHub 배포 확인 및 작업 중단/업데이트/복구.

설치: 프로젝트를 저장하고 Premiere를 닫은 뒤 Contentrium-CUT-Setup.exe를 실행하세요. 첫 설치 시 고정 FFmpeg 제공자 파일을 별도로 내려받습니다. Windows 실행 파일에는 Authenticode 인증서가 없으며 배포 manifest는 Ed25519로 검증합니다.

검증: 실제 Premiere 영상 컷과 연결 오디오 싱크 이동, frozen runtime 실행, Python 263개 및 Node 33개 시험 통과. Community-1 실추론/한국어 다인 장시간 인식 품질은 미검증입니다. 프로젝트 선택 파일 시작·수동 타임코드·매핑 영속화 등 잔여 사항은 docs/qa/round-1.md에 기록했습니다.
