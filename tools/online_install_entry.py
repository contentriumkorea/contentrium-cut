"""Version-independent online entry. CLI modes never install or open Premiere."""
import argparse
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
from contentrium_cut.online_installer import OnlineInstaller


def show_gui(config):
    import tkinter as tk
    from tkinter import messagebox
    window=tk.Tk();window.title('Contentrium CUT');window.geometry('530x330');window.resizable(False,False);window.configure(bg='#151515')
    tk.Label(window,text='CONTENTRIUM  CUT',bg='#151515',fg='#eeeeea',font=('Segoe UI',20)).pack(pady=(28,15))
    status=tk.StringVar(value='GitHub의 최신 안정 버전을 확인하고 내려받습니다.\n인터넷 연결이 필요합니다. 기존 설치는 패널의 업데이트를 이용하세요.')
    tk.Label(window,textvariable=status,bg='#151515',fg='#bbbbbb',wraplength=465,justify='left').pack(padx=28,pady=15)
    events=queue.Queue();cancel=threading.Event();running=[False];handoff=[False]
    def worker():
        try:
            with tempfile.TemporaryDirectory(prefix='Contentrium-CUT-online-') as directory:
                client=OnlineInstaller(config,Path(directory)/'verification')
                events.put(('status','최신 안정 버전과 서명을 확인하고 있습니다.'));selected=client.check();asset=selected['installer']
                events.put(('status',f"{selected['manifest']['appVersion']} 설치 준비 파일을 내려받고 있습니다.\n{asset['size']/1024/1024:.1f} MiB · 서명과 파일 해시를 검증합니다."))
                path=client.download(selected,Path(directory)/'Contentrium-CUT-Setup.exe',cancel.is_set)
                if cancel.is_set():return
                events.put(('handoff','검증된 설치 창에서 계속해 주세요.\n프로젝트를 저장하고 Premiere를 정상 종료한 뒤 설치하세요.'))
                code=client.launch(selected,path,cancel=cancel.is_set)
                events.put(('done','설치 창이 종료됐습니다. 설치·복구 상태는 해당 창의 안내를 확인하세요.' if code==0 else '설치 단계가 완료되지 않았습니다. 다시 실행하여 복구할 수 있습니다.'))
        except Exception as error:events.put(('error',getattr(error,'message','배포를 확인하거나 내려받지 못했습니다. 인터넷 연결을 확인하고 다시 시도해 주세요.')))
        finally:events.put(('idle',None))
    def begin():
        running[0]=True;cancel.clear();button.configure(state='disabled')
        threading.Thread(target=worker,name='CUT-online-download',daemon=False).start()
    button=tk.Button(window,text='최신 버전 다운로드 및 설치',command=begin,bg='#ddddda',fg='#151515',padx=20,pady=8);button.pack(pady=15)
    def close():
        if handoff[0]:messagebox.showinfo('Contentrium CUT','열려 있는 설치 창에서 설치를 마치거나 취소해 주세요.');return
        if running[0]:cancel.set();status.set('다운로드 취소를 기다리고 있습니다.');return
        window.destroy()
    def poll():
        try:
            while True:
                kind,value=events.get_nowait()
                if kind in {'status','error','done','handoff'}:status.set(value)
                if kind=='handoff':handoff[0]=True
                if kind=='idle':
                    running[0]=handoff[0]=False;button.configure(state='normal')
                    if cancel.is_set():window.destroy();return
        except queue.Empty:pass
        window.after(150,poll)
    window.protocol('WM_DELETE_WINDOW',close);poll();window.mainloop()


def main(argv=None):
    parser=argparse.ArgumentParser(description='Contentrium CUT small online installer')
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--check',action='store_true');mode.add_argument('--download-only',action='store_true')
    parser.add_argument('--destination');parser.add_argument('--result')
    args=parser.parse_args(argv)
    if args.download_only and not args.destination:parser.error('--download-only requires --destination')
    resources=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))
    config=json.loads((resources/'online-config.json').read_text(encoding='utf-8'))
    if not (args.check or args.download_only):show_gui(config);return 0
    try:
        with tempfile.TemporaryDirectory(prefix='Contentrium-CUT-check-') as directory:
            client=OnlineInstaller(config,directory);selected=client.check()
            report=dict(status='VERIFIED',appVersion=selected['manifest']['appVersion'],tag=selected['manifest']['tag'],releaseId=selected['release']['id'],signatureVerified=True,installer=selected['installer'],installationChanged=False)
            if args.download_only:
                path=client.download(selected,Path(args.destination));report.update(downloadPath=str(path),downloadHashVerified=True)
    except Exception as error:
        report=dict(status='FAILED',errorCode=getattr(error,'code','ONLINE_INSTALL_FAILED'),installationChanged=False)
    if args.result:Path(args.result).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    if sys.stdout:print(json.dumps(report))
    return 0 if report['status']=='VERIFIED' else 1


if __name__=='__main__':raise SystemExit(main())
