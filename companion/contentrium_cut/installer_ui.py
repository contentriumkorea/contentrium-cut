"""Shared monochrome Tk installer view. UI updates stay on the main thread."""
from .installer_reporting import ProgressMeter
from pathlib import Path
import sys

BG='#121212';CARD='#1c1c1c';FG='#f1f1ed';MUTED='#a4a4a0'


class InstallerWindow:
    def __init__(self,subtitle,*,tk=None,ttk=None):
        if tk is None:
            import tkinter as tk
            from tkinter import ttk
        self.tk=tk;self.window=tk.Tk();w=self.window
        w.title('Contentrium CUT');w.geometry('660x540');w.resizable(False,False);w.configure(bg=BG)
        # Explicit branding replaces Tk's feather. Transparency is a safe source
        # fallback if resources are absent; never fall back to the feather.
        icon=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))/'resources/cut.png'
        self.title_icon=tk.PhotoImage(file=str(icon)) if icon.is_file() else tk.PhotoImage(width=1,height=1)
        w.iconphoto(True,self.title_icon)
        header=tk.Frame(w,bg=BG);header.pack(fill='x',padx=32,pady=(30,22))
        tk.Label(header,text='C',bg=FG,fg=BG,font=('Segoe UI',20,'bold'),width=2).pack(side='left',padx=(0,14))
        brand=tk.Frame(header,bg=BG);brand.pack(side='left')
        tk.Label(brand,text='Contentrium CUT',bg=BG,fg=FG,font=('Segoe UI',20,'bold')).pack(anchor='w')
        tk.Label(brand,text=subtitle,bg=BG,fg=MUTED,font=('Segoe UI',10)).pack(anchor='w',pady=(3,0))
        self.steps=[];row=tk.Frame(w,bg=BG);row.pack(fill='x',padx=32,pady=(0,18))
        for label in ['01  배포 확인','02  다운로드','03  검증','04  설치·연결']:
            step=tk.Label(row,text=label,bg=BG,fg='#70706c',font=('Segoe UI',10));step.pack(side='left',padx=(0,21));self.steps.append(step)
        panel=tk.Frame(w,bg=CARD,highlightbackground='#343434',highlightthickness=1);panel.pack(fill='both',expand=True,padx=32)
        self.title_text=tk.StringVar(value='설치를 준비하세요');self.detail_text=tk.StringVar(value='최신 안정 버전의 파일을 안전하게 내려받습니다.')
        self.percent_text=tk.StringVar(value='—');self.metrics_text=tk.StringVar(value='인터넷 연결이 필요합니다.');self.error_text=tk.StringVar(value='')
        tk.Label(panel,textvariable=self.title_text,bg=CARD,fg=FG,font=('Segoe UI',16,'bold'),anchor='w').pack(fill='x',padx=24,pady=(24,10))
        tk.Label(panel,textvariable=self.detail_text,bg=CARD,fg='#c2c2bd',font=('Segoe UI',10),wraplength=535,justify='left',anchor='nw').pack(fill='x',padx=24)
        tk.Label(panel,textvariable=self.percent_text,bg=CARD,fg=FG,font=('Segoe UI',24),anchor='e').pack(fill='x',padx=24,pady=(16,8))
        style=ttk.Style(w)
        if 'clam' in style.theme_names():style.theme_use('clam')
        style.configure('CUT.Horizontal.TProgressbar',troughcolor='#343434',background=FG,bordercolor='#343434',lightcolor=FG,darkcolor=FG,thickness=6)
        self.progress=ttk.Progressbar(panel,style='CUT.Horizontal.TProgressbar',maximum=100,mode='determinate',value=0);self.progress.pack(fill='x',padx=24)
        tk.Label(panel,textvariable=self.metrics_text,bg=CARD,fg=MUTED,font=('Segoe UI',9),anchor='w').pack(fill='x',padx=24,pady=(10,10))
        tk.Label(panel,textvariable=self.error_text,bg=CARD,fg=FG,font=('Segoe UI',9),anchor='w').pack(fill='x',padx=24,pady=(0,16))
        footer=tk.Frame(w,bg=BG);footer.pack(fill='x',padx=32,pady=(18,14))
        self.copy_button=tk.Button(footer,text='진단 내용 복사',command=self.copy_diagnostics,bg=BG,fg=MUTED,relief='flat',borderwidth=0,state='disabled');self.copy_button.pack(side='left')
        self.primary_button=tk.Button(footer,text='다운로드 및 설치',bg=FG,fg=BG,activebackground='#d7d7d1',relief='flat',borderwidth=0,font=('Segoe UI',10,'bold'),padx=20,pady=10);self.primary_button.pack(side='right')
        self.cancel_button=tk.Button(footer,text='닫기',bg=BG,fg=MUTED,relief='flat',borderwidth=0,padx=14,pady=10);self.cancel_button.pack(side='right',padx=(0,8))
        tk.Label(w,text='원본·설정·모델은 보존됩니다.  ·  설치 파일은 서명과 해시를 검증합니다.',bg=BG,fg='#787874',font=('Segoe UI',9)).pack(anchor='w',padx=32,pady=(0,18))
        self.meter=ProgressMeter();self.diagnostic=''

    def phase(self,step,title,detail):
        self.title_text.set(title);self.detail_text.set(detail);self.percent_text.set('—');self.metrics_text.set('처리 중입니다. 이 단계는 실제 진행률을 측정하지 않습니다.')
        self.progress.stop();self.progress.configure(mode='indeterminate',value=0);self.progress.start(12)
        for index,label in enumerate(self.steps,1):label.configure(fg=FG if index==step else MUTED if index<step else '#70706c')

    def transfer(self,name,received,total):
        result=self.meter.update(name,received,total);self.title_text.set('파일을 내려받고 있습니다');self.detail_text.set(name)
        for index,label in enumerate(self.steps,1):label.configure(fg=FG if index==2 else MUTED if index<2 else '#70706c')
        percent=result['percent'];self.progress.stop()
        if percent is None:self.progress.configure(mode='indeterminate',value=0);self.progress.start(12);self.percent_text.set('—')
        else:self.progress.configure(mode='determinate',value=percent);self.percent_text.set(f'{percent:.0f}%')
        text=f'{received/1024/1024:.1f} MB'
        if total:text+=f' / {total/1024/1024:.1f} MB'
        if result['bytesPerSecond']>0:text+=f"  ·  {result['bytesPerSecond']/1024/1024:.1f} MB/s"
        if result['remainingSeconds'] is not None:text+=f"  ·  약 {max(1,round(result['remainingSeconds']))}초 남음"
        self.metrics_text.set(text)

    def failure(self,report,log_path=None):
        error=report['error'];self.progress.stop();self.progress.configure(mode='determinate',value=0)
        self.title_text.set('설치를 계속하지 못했습니다' if report['status']!='CANCELED' else '설치를 중단했습니다')
        self.detail_text.set(error['message']+'\n'+error['hint']);self.percent_text.set('—')
        self.error_text.set('오류 코드  '+error['code']+(f"  ·  종료 코드 {report['exitCode']}" if report.get('exitCode') is not None else ''))
        self.metrics_text.set('진단 내용을 복사해 전달하면 실패 단계를 확인할 수 있습니다.')
        self.diagnostic='Contentrium CUT '+str(report.get('appVersion') or '')+'\n'+str(report.get('phase') or '')+'\n'+self.error_text.get()+'\n'+error['message']+'\n'+error['hint']
        if log_path:self.diagnostic+='\n진단 기록: '+str(log_path)
        self.copy_button.configure(state='normal');self.primary_button.configure(text='다시 시도',state='normal')

    def copy_diagnostics(self):
        self.window.clipboard_clear();self.window.clipboard_append(self.diagnostic)

    def ready(self,title,detail):
        self.progress.stop();self.progress.configure(mode='determinate',value=0);self.percent_text.set('—');self.title_text.set(title);self.detail_text.set(detail);self.metrics_text.set('')
