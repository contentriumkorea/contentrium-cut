"""Small, private-data-free installer reports and measured transfer presentation."""
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import uuid

HINTS={
    'INSTALL_REGISTRATION':'기존 설치 등록 확인이 필요합니다. 오류 코드를 전달해 주세요. 기존 폴더를 임의로 삭제하지 마세요.',
    'INSTALL_LOCATION':'설치 폴더 접근 권한과 사용자 폴더의 연결/리디렉션 설정을 확인해 주세요.',
    'HOST_RUNNING':'프로젝트를 저장하고 모든 Premiere Pro 창을 정상 종료한 뒤 다시 시도하세요.',
    'UPDATE_HOST_RUNNING':'프로젝트를 저장하고 모든 Premiere Pro 창을 정상 종료한 뒤 다시 시도하세요.',
    'UPDATE_HASH':'파일 검증이 일치하지 않습니다. 다시 다운로드해 주세요. 기존 설치는 유지합니다.',
    'UPDATE_HTTP':'인터넷 연결과 GitHub 접근을 확인한 뒤 다시 시도하세요.',
    'INSTALL_COMPATIBILITY':'Windows x64와 지원되는 Adobe/Premiere 환경인지 확인해 주세요.',
    'UPIA_MISSING':'Adobe Creative Cloud 데스크톱 앱과 Premiere Pro 설치 상태를 확인해 주세요. Adobe 패널 등록 도구를 찾지 못했습니다.',
    'UPIA_LIST_UNREADABLE':'Adobe Creative Cloud의 패널 등록 상태를 확인하지 못했습니다. 오류 코드와 진단 내용을 전달해 주세요.',
    'SETUP_RESULT_MISSING':'전체 설치 프로그램이 확인 결과를 남기지 못했습니다. 표시된 종료 코드와 진단 내용을 전달해 주세요.',
    'SETUP_STARTUP':'설치 프로그램 시작 중 오류가 발생했습니다. 오류 코드와 예외 유형을 전달해 주세요.',
    'CANCELED':'같은 설치 파일로 다시 실행하여 보존된 설치 준비를 이어갈 수 있습니다.',
}


def failure(error,phase='startup',*,version=None,exit_code=None):
    code=getattr(error,'code','SETUP_STARTUP')
    if not isinstance(code,str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}',code):code='SETUP_STARTUP'
    # Exception text can contain paths, tokens or command output. Keep only
    # trusted domain messages; diagnostics on disk retain no exception text.
    message=getattr(error,'message',None)
    if not isinstance(message,str):message='설치 프로그램을 시작하거나 완료하지 못했습니다.'
    return dict(schemaVersion=1,status='CANCELED' if code=='CANCELED' else 'FAILED',appVersion=version,
                phase=phase,error=dict(code=code,message=message[:1200],hint=HINTS.get(code,'오류 코드와 진단 내용을 전달해 주세요. 기존 설치와 자료는 보존합니다.'),exceptionType=type(error).__name__),exitCode=exit_code)


def _guard(path):
    for item in (path,*path.parents):
        try:info=item.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:raise ValueError('Linked report path')


def write_report(path,report):
    path=Path(path).absolute();_guard(path)
    raw=(json.dumps(report,ensure_ascii=False,allow_nan=False)+'\n').encode('utf-8')
    if len(raw)>32768:raise ValueError('Installer report too large')
    path.parent.mkdir(parents=True,exist_ok=True);pending=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with pending.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        _guard(path);os.replace(pending,path)
    finally:pending.unlink(missing_ok=True)


def read_outcome(path,exit_code,*,expected_version):
    try:
        path=Path(path);_guard(path)
        if path.stat().st_size>32768:raise ValueError()
        report=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(report,dict) or type(report.get('schemaVersion')) is not int or report['schemaVersion']!=1 or report.get('appVersion')!=expected_version:raise ValueError()
        if report.get('status') not in {'FAILED','CANCELED','READY_TO_OPEN','PENDING_PROVISIONING','OPENED','DIAGNOSED'}:raise ValueError()
        if report['status'] in {'FAILED','CANCELED'}:
            error=report.get('error',{})
            if not isinstance(error,dict) or not all(isinstance(error.get(k),str) for k in ['code','message','hint','exceptionType']):raise ValueError()
        elif exit_code!=0 and report['status']!='CANCELED':raise ValueError()
        report['exitCode']=exit_code
        return report
    except (ValueError,TypeError,KeyError,OSError):
        class Missing(Exception):code='SETUP_RESULT_MISSING';message='전체 설치 창이 확인 결과를 남기지 않고 종료됐습니다.'
        return failure(Missing(),'handoff',version=expected_version,exit_code=exit_code)


def child_environment(source=None):
    result=dict(os.environ if source is None else source)
    for key in list(result):
        if key.startswith('_PYI_') or key in {'TCL_LIBRARY','TK_LIBRARY','PYTHONHOME','PYTHONPATH'}:result.pop(key)
    result['PYINSTALLER_RESET_ENVIRONMENT']='1'
    return result


def save_diagnostic(report,*,directory=None):
    directory=Path(directory or Path(tempfile.gettempdir())/'Contentrium CUT Install Logs');_guard(directory);directory.mkdir(parents=True,exist_ok=True)
    record={key:report.get(key) for key in ['schemaVersion','status','appVersion','phase','exitCode']}
    record['error']={key:report.get('error',{}).get(key) for key in ['code','exceptionType']}
    handle,path=tempfile.mkstemp(prefix='CUT-install-',suffix='.json',dir=directory);os.close(handle)
    write_report(path,record);return Path(path)


class ProgressMeter:
    def __init__(self,clock=time.monotonic):self.clock=clock;self.name=None;self.started=0;self.baseline=0;self.last=0
    def update(self,name,received,total,*,now=None):
        now=self.clock() if now is None else now
        if name!=self.name or received<self.last:self.name=name;self.started=now;self.baseline=received
        self.last=received
        elapsed=now-self.started;speed=(received-self.baseline)/elapsed if elapsed>0 else 0
        percent=min(100,received*100/total) if isinstance(total,int) and total>0 else None
        return dict(name=name,received=received,total=total,percent=percent,bytesPerSecond=speed,
                    remainingSeconds=(max(0,total-received)/speed if total and speed>0 else None))
