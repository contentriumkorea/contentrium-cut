import importlib
from types import SimpleNamespace
import unittest
from contentrium_cut.installer_reporting import failure
from contentrium_cut.contract import CutError


class Variable:
    def __init__(self,value='',**kw):self.value=value
    def set(self,value):self.value=value
    def get(self):return self.value


class Widget:
    def __init__(self,*args,**kw):self.options=kw;self.running=False;self.icons=[];self.clipboard=''
    def pack(self,*args,**kw):return self
    def grid(self,*args,**kw):return self
    def configure(self,*args,**kw):self.options.update(kw)
    config=configure
    def title(self,value):self.options['title']=value
    def geometry(self,value):self.options['geometry']=value
    def resizable(self,*args):pass
    def iconphoto(self,*args):self.icons.append(args)
    def protocol(self,*args):pass
    def start(self,*args):self.running=True
    def stop(self):self.running=False
    def clipboard_clear(self):self.clipboard=''
    def clipboard_append(self,text):self.clipboard+=text
    def theme_use(self,*a):return 'clam'
    def theme_names(self):return ['clam']


FAKE=SimpleNamespace(Tk=Widget,Frame=Widget,Label=Widget,Button=Widget,PhotoImage=Widget,StringVar=Variable,Progressbar=Widget,Style=Widget)


class InstallerViewTests(unittest.TestCase):
    def view(self):
        try:module=importlib.import_module('contentrium_cut.installer_ui')
        except ModuleNotFoundError:self.fail('Shared progress view is missing')
        return module.InstallerWindow('온라인 설치',tk=FAKE,ttk=FAKE)

    def test_download_has_actual_percent_bytes_and_no_default_feather(self):
        view=self.view();view.transfer('Setup.exe',40*1024*1024,100*1024*1024)
        self.assertEqual(view.progress.options['value'],40)
        self.assertEqual(view.percent_text.get(),'40%');self.assertIn('40.0',view.metrics_text.get())
        self.assertTrue(view.window.icons,'Explicit iconphoto removes the Tk feather')

    def test_unmeasured_verification_and_panel_connection_never_fake_completion(self):
        view=self.view();view.transfer('Setup.exe',100,100)
        view.phase(3,'파일 검증','서명과 해시를 확인합니다.')
        self.assertEqual(view.progress.options['mode'],'indeterminate');self.assertEqual(view.percent_text.get(),'—')
        view.phase(4,'패널 연결 대기','Premiere에서 Contentrium CUT을 열어 주세요.')
        self.assertEqual(view.percent_text.get(),'—');self.assertIn('열어',view.detail_text.get())

    def test_failure_has_retry_and_copyable_code_and_hint(self):
        view=self.view();report=failure(CutError('INSTALL_REGISTRATION','등록 확인 실패'),'location',version='0.1.109')
        view.failure(report)
        self.assertIn('INSTALL_REGISTRATION',view.error_text.get());self.assertIn('삭제하지',view.detail_text.get())
        self.assertEqual(view.primary_button.options['text'],'다시 시도');self.assertEqual(view.primary_button.options['state'],'normal')
        view.copy_diagnostics();self.assertIn('INSTALL_REGISTRATION',view.window.clipboard)


if __name__=='__main__':unittest.main()
