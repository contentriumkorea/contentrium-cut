"""Version-independent online entry. CLI modes never install or open Premiere."""
import argparse
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
from contentrium_cut.online_installer import OnlineInstaller
from contentrium_cut.contract import CutError


def run_online_worker(config,events,cancel,*,client_factory=OnlineInstaller):
    from contentrium_cut.installer_reporting import failure,read_outcome,save_diagnostic
    version=None;phase='check'
    try:
        with tempfile.TemporaryDirectory(prefix='Contentrium-CUT-online-') as directory:
            client=client_factory(config,Path(directory)/'verification')
            events.put(('phase',dict(step=1,title='최신 배포를 확인합니다',detail='GitHub의 안정 버전과 서명을 확인하고 있습니다.')))
            selected=client.check();version=selected['manifest']['appVersion'];phase='download'
            def transferred(received,total):events.put(('transfer',dict(name='Contentrium CUT '+version+' 설치 준비 파일',received=received,total=total)))
            path=client.download(selected,Path(directory)/'Contentrium-CUT-Setup.exe',cancel.is_set,progress=transferred)
            events.put(('phase',dict(step=3,title='파일을 검증합니다',detail='선택한 버전의 서명과 SHA-256을 확인하고 있습니다.')))
            if cancel.is_set():raise CutError('CANCELED','설치 창을 열기 전에 취소했습니다.')
            phase='handoff';result=Path(directory)/'setup-result.json'
            events.put(('phase',dict(step=4,title='전체 설치 창을 준비합니다',detail='실행 직전 배포 정보와 파일을 다시 확인합니다.')))
            def launched():events.put(('handoff',dict(step=4,title='전체 설치 창에서 계속하세요',detail='새로 열린 설치 창에 다운로드 진행률과 오류 원인이 표시됩니다. 프로젝트를 저장하고 Premiere를 정상 종료해 주세요.')))
            code=client.launch(selected,path,cancel=cancel.is_set,result_path=result,before_launch=launched)
            report=read_outcome(result,code,expected_version=version)
            if report['status'] in {'FAILED','CANCELED'}:
                try:log=save_diagnostic(report)
                except (OSError,ValueError):log=None
                events.put(('failed',dict(report=report,log=str(log) if log else None)))
            elif report['status'] in {'READY_TO_OPEN','PENDING_PROVISIONING','OPENED'}:events.put(('ready',report))
            else:raise CutError('SETUP_RESULT_MISSING','전체 설치의 상태를 확인하지 못했습니다.')
    except Exception as error:
        report=failure(error,phase,version=version)
        try:log=save_diagnostic(report)
        except (OSError,ValueError):log=None
        events.put(('failed',dict(report=report,log=str(log) if log else None)))
    finally:events.put(('idle',None))


def show_gui(config):
    from contentrium_cut.installer_ui import InstallerWindow
    view=InstallerWindow('온라인 설치');window=view.window;events=queue.Queue();cancel=threading.Event();running=[False];handoff=[False];closing=[False]
    view.ready('최신 Contentrium CUT을 설치하세요','필요한 파일을 내려받고 검증한 뒤 전체 설치 창으로 이어집니다. 기존 설치는 패널의 업데이트를 이용하세요.')
    def begin():
        running[0]=True;closing[0]=False;cancel.clear();handoff[0]=False;view.error_text.set('');view.copy_button.configure(state='disabled')
        view.primary_button.configure(state='disabled');view.cancel_button.configure(text='취소',state='normal')
        threading.Thread(target=run_online_worker,args=(config,events,cancel),name='CUT-online-download',daemon=False).start()
    def close():
        if handoff[0]:
            view.detail_text.set('열려 있는 전체 설치 창에서 설치를 마치거나 취소해 주세요. 이 창은 결과를 기다리고 있습니다.');return
        if running[0]:
            closing[0]=True;cancel.set();view.phase(2,'다운로드를 중단하고 있습니다','현재 네트워크 읽기가 끝나는 대로 안전하게 중단합니다.');view.cancel_button.configure(state='disabled');return
        window.destroy()
    view.primary_button.configure(command=begin);view.cancel_button.configure(command=close)
    def poll():
        try:
            while True:
                kind,value=events.get_nowait()
                if kind=='transfer':view.transfer(value['name'],value['received'],value['total'])
                elif kind in {'phase','handoff'}:
                    view.phase(value['step'],value['title'],value['detail'])
                    if kind=='handoff':handoff[0]=True
                elif kind=='failed':view.failure(value['report'],value['log']);view.primary_button.configure(command=begin)
                elif kind=='ready':
                    view.ready('기존 설치를 열었습니다' if value['status']=='OPENED' else 'Premiere에서 연결을 마무리하세요',
                              '새 버전은 기존 패널의 업데이트로 설치하세요.' if value['status']=='OPENED' else 'Window → UXP Plugins → Contentrium CUT 패널을 열어 첫 연결을 확인해 주세요. 연결 준비 전에는 완료로 표시하지 않습니다.')
                    view.primary_button.configure(text='닫기',command=window.destroy,state='normal')
                elif kind=='idle':
                    running[0]=handoff[0]=False;view.primary_button.configure(state='normal');view.cancel_button.configure(text='닫기',state='normal')
                    if closing[0]:window.destroy();return
        except queue.Empty:pass
        window.after(120,poll)
    window.protocol('WM_DELETE_WINDOW',close);poll();window.mainloop()

def main(argv=None):
    parser=argparse.ArgumentParser(description='Contentrium CUT small online installer')
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--check',action='store_true');mode.add_argument('--download-only',action='store_true');mode.add_argument('--diagnose-setup',action='store_true',help='Download and run the signed Setup read-only startup diagnosis; never install.')
    parser.add_argument('--destination');parser.add_argument('--result');parser.add_argument('--diagnostic-root')
    args=parser.parse_args(argv)
    if args.download_only and not args.destination:parser.error('--download-only requires --destination')
    resources=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))
    config=json.loads((resources/'online-config.json').read_text(encoding='utf-8'))
    if not (args.check or args.download_only or args.diagnose_setup):show_gui(config);return 0
    try:
        with tempfile.TemporaryDirectory(prefix='Contentrium-CUT-check-') as directory:
            client=OnlineInstaller(config,directory);selected=client.check()
            report=dict(status='VERIFIED',appVersion=selected['manifest']['appVersion'],tag=selected['manifest']['tag'],releaseId=selected['release']['id'],signatureVerified=True,installer=selected['installer'],installationChanged=False)
            if args.download_only:
                path=client.download(selected,Path(args.destination));report.update(downloadPath=str(path),downloadHashVerified=True)
            if args.diagnose_setup:
                from contentrium_cut.installer_reporting import read_outcome
                path=client.download(selected,Path(directory)/'Contentrium-CUT-Setup.exe')
                outcome=Path(directory)/'diagnosis.json'
                code=client.launch(selected,path,result_path=outcome,diagnose=True,diagnostic_root=args.diagnostic_root)
                diagnosis=read_outcome(outcome,code,expected_version=report['appVersion'])
                report.update(setupDiagnosis=diagnosis,downloadHashVerified=True)
                if diagnosis['status']!='DIAGNOSED' or diagnosis.get('installationChanged') is not False:report['status']='FAILED'
    except Exception as error:
        report=dict(status='FAILED',errorCode=getattr(error,'code','ONLINE_INSTALL_FAILED'),installationChanged=False)
    if args.result:Path(args.result).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    if sys.stdout:print(json.dumps(report))
    return 0 if report['status']=='VERIFIED' else 1


if __name__=='__main__':raise SystemExit(main())
