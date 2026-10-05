"""Native Companion window and one-time updater handoff; no Premiere termination."""
import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog
from .contract import CutError
from .models import ModelManager
from .service import CutService
from .windows_install import WindowsInstallation, WindowsNamedMutex, windows_process_snapshot, process_identity

def process_probe(instance,claim):
    rows=[r for r in windows_process_snapshot() if r.get('imageName','').lower() in {'adobe premiere pro.exe','premiere pro.exe'}]
    if claim and isinstance(claim,dict):rows=[r for r in rows if r.get('pid')==claim.get('pid')]
    if len(rows)!=1:return None
    actual=process_identity(rows[0]['pid'])
    if not actual:return None
    if claim and claim.get('createTime') and claim['createTime']!=actual['createdAtTicks']:return None
    return {**actual,'createTime':actual['createdAtTicks'],'alive':True,'processName':'Adobe Premiere Pro'}

def read_config(path=None):
    directory=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]
    candidate=Path(path) if path else directory/'config.json'
    if not candidate.is_file():candidate=Path(getattr(sys,'_MEIPASS',directory))/'config.json'
    value=json.loads(candidate.read_text(encoding='utf-8-sig'))
    return value,candidate

class CompanionWindow:
    def __init__(self,root,config,config_path,mutex):
        self.root=root;self.config=config;self.config_path=config_path;self.mutex=mutex;self.events=queue.Queue();self.handing_off=False;self.started_jobs=set();self.service=None
        self.window=tk.Tk();self.window.title('Contentrium CUT');self.window.geometry('540x760');self.window.minsize(480,650);self.window.configure(bg='#141414')
        canvas=tk.Canvas(self.window,bg='#141414',highlightthickness=0)
        scrollbar=tk.Scrollbar(self.window,orient='vertical',command=canvas.yview)
        scrollbar.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        self.body=tk.Frame(canvas,bg='#141414');body_id=canvas.create_window((26,24),window=self.body,anchor='nw')
        self.body.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(body_id,width=max(400,e.width-52)))
        self.window.bind('<MouseWheel>',lambda e:canvas.yview_scroll(-int(e.delta/120),'units'))
        self.text('CONTENTRIUM  CUT',size=20,color='#eeeeea').pack(anchor='w');self.text('MONO STUDIO   /   '+config['appVersion'],size=9,color='#777777').pack(anchor='w',pady=(5,18))
        self.code=tk.StringVar(value='연결 준비 중');self.text('PREMIERE CONNECTION',size=9).pack(anchor='w');tk.Label(self.body,textvariable=self.code,font=('Consolas',28),fg='#eeeee8',bg='#1e1e1e',pady=12).pack(fill='x',pady=8)
        self.text('Premiere 패널에 위 연결 코드를 입력하세요. 코드는 2분 동안 유효합니다.',size=9).pack(anchor='w');self.button('새 연결 코드',self.new_code).pack(fill='x',pady=(8,16))
        self.status=tk.StringVar(value='로컬 엔진 준비 중');tk.Label(self.body,textvariable=self.status,font=('Segoe UI',10),fg='#b9b9b4',bg='#141414',wraplength=470,justify='left').pack(fill='x',anchor='w',pady=12)
        self.text('LOCAL MODELS',size=9).pack(anchor='w');self.models=tk.StringVar();tk.Label(self.body,textvariable=self.models,font=('Segoe UI',10),fg='#ddd',bg='#141414',justify='left').pack(anchor='w',pady=8)
        self.text('Hugging Face 접근 토큰 · 저장하지 않음',size=9).pack(anchor='w');self.token=tk.Entry(self.body,show='•',bg='#242424',fg='#eee',insertbackground='#fff',relief='flat');self.token.pack(fill='x',ipady=7,pady=5)
        self.text('Community-1 고정 리비전 · 40자리 SHA',size=9).pack(anchor='w');self.revision=tk.Entry(self.body,bg='#242424',fg='#eee',insertbackground='#fff',relief='flat');self.revision.pack(fill='x',ipady=7,pady=5)
        self.terms=tk.BooleanVar(value=False);tk.Checkbutton(self.body,text='내 계정에서 제공자의 이용 조건에 동의했습니다.',variable=self.terms,bg='#141414',fg='#aaa',selectcolor='#2a2a2a',activebackground='#141414',activeforeground='#fff').pack(anchor='w')
        row=tk.Frame(self.body,bg='#141414');row.pack(fill='x',pady=6);self.button('리비전 확인',self.fetch_revision,parent=row).pack(side='left',expand=True,fill='x',padx=(0,4));self.button('화자 모델 설치',self.install_model,parent=row).pack(side='left',expand=True,fill='x',padx=(4,0))
        self.button('제공자 모델 페이지 열기',lambda:self.open_url('https://huggingface.co/pyannote/speaker-diarization-community-1')).pack(fill='x',pady=5)
        self.button('로컬 FFmpeg 경로 설정',self.choose_ffmpeg).pack(fill='x',pady=5)
        self.update=tk.StringVar(value='업데이트 확인 대기');tk.Label(self.body,textvariable=self.update,font=('Segoe UI',10),fg='#bbb',bg='#141414',wraplength=470,justify='left').pack(fill='x',pady=(18,7))
        row2=tk.Frame(self.body,bg='#141414');row2.pack(fill='x');self.button('업데이트 확인',self.check_update,parent=row2).pack(side='left',expand=True,fill='x',padx=(0,4));self.update_button=self.button('업데이트',self.start_update,parent=row2);self.update_button.pack(side='left',expand=True,fill='x',padx=(4,0))
        self.button('중단된 업데이트 복구',self.recover).pack(fill='x',pady=6)
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.window.after(500,self.tick)

    def text(self,value,size=10,color='#999'):return tk.Label(self.body,text=value,font=('Segoe UI',size),fg=color,bg='#141414',justify='left')
    def button(self,value,command,parent=None):return tk.Button(parent or self.body,text=value,command=command,bg='#2c2c2c',fg='#ddd',activebackground='#444',activeforeground='#fff',relief='flat',font=('Segoe UI',10),padx=10,pady=7)
    def background(self,callback):
        if self.handing_off:return
        def run():
            try:callback()
            except CutError as e:self.events.put(('status',e.code+' · '+e.message))
            except Exception:self.events.put(('status','작업을 마치지 못했습니다. 설정 및 연결 상태를 확인하세요.'))
        threading.Thread(target=run,daemon=True).start()
    def open_url(self,url):
        import webbrowser
        webbrowser.open(url)
    def new_code(self):
        if self.service:self.code.set(self.service.auth.issue_code())
    def fetch_revision(self):
        token=self.token.get().strip()
        def run():
            from huggingface_hub import HfApi
            revision=HfApi().model_info('pyannote/speaker-diarization-community-1',token=token or None).sha
            self.events.put(('revision',revision))
        self.background(run)
    def install_model(self):
        token=self.token.get().strip();revision=self.revision.get().strip()
        if not token or not self.terms.get():self.status.set('제공자 계정의 접근 권한과 이용 조건 동의를 확인하세요.');return
        if len(revision)!=40 or any(c not in '0123456789abcdefABCDEF' for c in revision):self.status.set('리비전 확인 후 40자리 SHA를 입력하세요.');return
        self.token.delete(0,'end')
        epoch=self.service.epoch
        def run():
            job=self.service.jobs.submit('model-setup',{'modelRoot':str(self.root/'models'),'token':token,'termsAccepted':True,'revision':revision},expected_epoch=epoch);self.started_jobs.add(job['jobId']);self.events.put(('status','Community-1을 로컬에 설치하고 있습니다.'))
        self.background(run)
    def choose_ffmpeg(self):
        path=filedialog.askopenfilename(title='ffmpeg.exe 선택',filetypes=[('FFmpeg','ffmpeg.exe')])
        if path and Path(path).with_name('ffprobe.exe').is_file():
            from .jobs import atomic_json
            saved=self.root/'settings.json';settings=json.loads(saved.read_text(encoding='utf-8')) if saved.is_file() else {}
            settings['ffmpeg']=str(Path(path).resolve());atomic_json(saved,settings);os.environ['CONTENTRIUM_FFMPEG']=path;self.status.set('로컬 FFmpeg 경로가 저장됐습니다.')
    def check_update(self):
        if self.service.updater:self.background(self.service.updater.check)
    def start_update(self):
        candidate=self.service.updater.state().get('candidate') if self.service.updater else None
        if not candidate:self.update.set('사용 가능한 업데이트를 먼저 확인하세요.');return
        self.background(lambda:self.service.updater.start(candidate['candidateId'],candidate['manifestDigest'],'native-'+str(time.time_ns())))
    def recover(self):
        if self.service.updater:self.background(self.service.updater.recover)
    def handoff(self):
        self.handing_off=True
        try:
            self.service.quiesce_for_handoff()
            ticket=self.service.updater.create_activation_handoff();active=json.loads((self.root/'app'/'active.json').read_text(encoding='utf-8'));exe=self.root/'app'/active['versionDirectory']/'Contentrium CUT.exe'
            self.mutex.close()
            subprocess.Popen([str(exe),'--activation-handoff',ticket['token'],'--activation-update-id',ticket['updateId']],creationflags=subprocess.CREATE_NO_WINDOW,close_fds=True)
            self.window.destroy()
        except Exception:
            self.status.set('새 앱 실행이 중단됐습니다. Contentrium CUT을 다시 열고 업데이트 복구를 진행하세요.')
    def tick(self):
        if self.handing_off:return
        while not self.events.empty():
            kind,value=self.events.get_nowait()
            if kind=='status':self.status.set(value)
            elif kind=='revision':self.revision.delete(0,'end');self.revision.insert(0,value)
        if self.service:
            try:
                s=self.service.state();self.models.set('Silero  '+s['models']['silero']['status']+'\nCommunity-1  '+s['models']['community-1']['status'])
                for job_id in tuple(self.started_jobs):
                    job=self.service.jobs.get(job_id)
                    if job['status'] in {'completed','failed','canceled','interrupted'}:self.started_jobs.remove(job_id);self.status.set('모델 설치 '+job['status']+(' · '+job.get('error',{}).get('code','') if job.get('error') else ''))
                update=s['update'];candidate=update.get('candidate');self.update.set('새 버전 '+candidate['appVersion']+' 사용 가능' if candidate else '업데이트 · '+update['checkState']+' / '+update['updateState']);self.update_button.configure(state='normal' if candidate and update['checkState']=='AVAILABLE' else 'disabled')
                if update['updateState']=='WAITING_HOST_EXIT':self.status.set('모든 작업을 중단했습니다. Premiere Pro를 정상 종료하면 설치가 계속됩니다.')
                if update['updateState']=='PENDING_ACTIVATION':
                    if update.get('newVersion')!=self.config['appVersion']:self.handoff();return
                    self.status.set('Premiere에서 Contentrium CUT 패널을 열면 새 버전 연결을 확인합니다.')
                    self.service.finalize_activation()
            except CutError as e:self.status.set(e.code+' · '+e.message)
            except Exception:self.status.set('로컬 상태 확인에 실패했습니다. 앱을 다시 실행하세요.')
        self.window.after(500,self.tick)
    def close(self):
        if self.handing_off and self.service and self.service.closed.is_set():
            self.mutex.close();self.window.destroy();return
        if self.service and self.service.updater and not self.service.updater.gate_open:
            self.status.set('업데이트 창은 설치 또는 복구가 끝난 뒤 닫아 주세요.');return
        if self.service:self.service.close()
        self.mutex.close();self.window.destroy()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config');parser.add_argument('--activation-handoff');parser.add_argument('--activation-update-id');parser.add_argument('--probe',action='store_true');parser.add_argument('--probe-output');args=parser.parse_args()
    config,config_path=read_config(args.config)
    if args.probe:
        from .audio import analyze_audio
        from .models import SileroVAD,CommunityDiarizer
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        import torch,onnxruntime
        from pyannote.audio import Pipeline
        value=json.dumps({'appVersion':config['appVersion'],'bundleId':config['bundleId'],'runtimeReady':True,'torchVersion':torch.__version__,'onnxVersion':onnxruntime.__version__})
        if args.probe_output:Path(args.probe_output).write_text(value,encoding='utf-8')
        elif sys.stdout:print(value)
        return
    root=Path(os.environ['LOCALAPPDATA'])/'Contentrium CUT';root.mkdir(parents=True,exist_ok=True)
    try:
        mutex=WindowsNamedMutex(root).__enter__()
    except CutError:
        return  # Existing native window and engine remain the owner.
    window=CompanionWindow(root,config,config_path,mutex)
    try:
        saved=root/'settings.json'
        if saved.is_file():
            settings=json.loads(saved.read_text(encoding='utf-8'));ffmpeg=settings.get('ffmpeg')
            if ffmpeg and Path(ffmpeg).is_file():os.environ['CONTENTRIUM_FFMPEG']=ffmpeg
        packaged=Path(getattr(sys,'_MEIPASS',config_path.parent))/'silero'
        if ModelManager(root/'models').state('silero')['status']=='not_installed' and packaged.is_dir():shutil.copytree(packaged,root/'models'/'silero')
        def activation_probe(version,bundle,receipt):
            if config['appVersion']!=version or config['bundleId']!=bundle or not getattr(sys,'frozen',False):return False
            expected=(root/'app'/'versions'/version/'Contentrium CUT.exe').resolve()
            if Path(sys.executable).resolve()!=expected:return False
            return any(p.get('appVersion')==version and p.get('bundleId')==bundle and p.get('host') for p in window.service.panels.values())
        installation=WindowsInstallation(root,activation_probe=activation_probe)
        if args.activation_handoff:
            journal=json.loads((root/'updates'/'journal.json').read_text(encoding='utf-8'))
            if args.activation_update_id!=journal.get('updateId'):raise CutError('UPDATE_HANDOFF','Update identity does not match the native handoff.')
        runtime_config={**config,'activationHandoff':args.activation_handoff}
        service=CutService(root,runtime_config,installation,process_probe);window.service=service;service.start();window.new_code();window.status.set('Premiere Pro의 Contentrium CUT 패널에서 연결하세요.');window.check_update()
    except Exception:
        window.status.set('로컬 엔진을 시작하지 못했습니다. 동일 앱이 실행 중인지 확인하세요.')
    window.window.mainloop()
