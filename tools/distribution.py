"""Extractable manual distribution; existing updater assets remain unchanged."""
import argparse
import os
import uuid
from pathlib import Path
import zipfile
from contentrium_cut.updater import validate_zip

PORTABLE='Contentrium-CUT-Portable-Windows-x64.zip'
ONLINE='Contentrium-CUT-Install.exe'


def build_portable(output,version):
    output=Path(output);target=output/PORTABLE
    if target.exists():raise RuntimeError('Preserve existing extractable distribution; never replace it.')
    runtime=output/'Contentrium-CUT-Windows-x64.zip';panel=output/'Contentrium-CUT.ccx'
    if not panel.is_file():raise FileNotFoundError(panel)
    validate_zip(runtime)
    guide=f'''Contentrium CUT {version} — Windows x64 압축형 수동 구성

이 ZIP은 분석 엔진 전체와 같은 버전의 Premiere 패널을 포함합니다.
Premiere 플러그인이므로 완전 무설치·무등록 프로그램이 아닙니다.
Contentrium CUT.exe는 백그라운드 분석 엔진이며 별도 편집 화면이 없습니다.

처음 사용하는 PC: 아래의 작은 온라인 설치기로 패널 등록과 보안 연결을 준비하세요.
https://github.com/contentriumkorea/contentrium-cut/releases/latest/download/Contentrium-CUT-Install.exe
설치 창에서 Premiere 정상 종료 안내를 따른 뒤, Premiere의
Window → UXP Plugins → Contentrium CUT 패널을 직접 열어 연결을 마칩니다.
기존 설치가 있으면 패널의 업데이트를 사용하세요.

Premiere/Contentrium-CUT.ccx: 같은 버전의 수동 패널 등록 구성요소입니다.
분석 엔진 폴더 복사만으로 인증된 기존 설치를 대체하거나 연결할 수 없습니다.
설정·모델·사용자 자료는 이 공개 ZIP에 들어 있지 않습니다.
혼합 녹음 모델은 이용자 권한과 동의가 필요하며 별도로 준비합니다.
FFmpeg는 Setup에서 제공자의 고정 패키지를 내려받으므로 인터넷이 필요합니다.

실제 Premiere 편집·첫 연결·Community-1 실제 품질은 이 패키지의
CLI 실행 검증만으로 확인된 것으로 간주하지 않습니다.
'''
    stage=target.with_name(target.name+'.'+uuid.uuid4().hex+'.stage')
    try:
        with zipfile.ZipFile(runtime) as source:
            if 'Contentrium CUT.exe' not in source.namelist():raise RuntimeError('Runtime executable is missing')
            with zipfile.ZipFile(stage,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as destination:
                for info in source.infolist():
                    if info.is_dir():continue
                    with source.open(info) as entry,destination.open(info.filename,'w',force_zip64=True) as result:
                        import shutil
                        shutil.copyfileobj(entry,result,1024*1024)
                destination.write(panel,'Premiere/Contentrium-CUT.ccx')
                destination.writestr('README.txt',guide.encode('utf-8-sig'))
        validate_zip(stage)
        # Windows rename refuses an existing destination, preserving earlier files.
        if target.exists():raise RuntimeError('Extractable output appeared concurrently')
        os.rename(stage,target)
    finally:stage.unlink(missing_ok=True)
    return target


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--version',required=True);args=parser.parse_args()
    print(build_portable(Path(__file__).resolve().parents[1]/'output'/args.version,args.version))
