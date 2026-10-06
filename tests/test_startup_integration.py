from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from contentrium_cut import integration
from contentrium_cut.contract import CutError


class Registry:
    HKEY_CURRENT_USER=REG_SZ=1
    def __init__(self): self.values={}
    def CreateKey(self,hive,key): return nullcontext(key)
    def OpenKey(self,hive,key): return nullcontext(key)
    def QueryValueEx(self,key,name):
        if (key,name) not in self.values: raise FileNotFoundError()
        return self.values[key,name],self.REG_SZ
    def SetValueEx(self,key,name,reserved,kind,value): self.values[key,name]=value


class StartupTests(unittest.TestCase):
    def test_startup_is_fixed_logon_command_and_conflicts_never_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            registry=Registry()
            command=integration.startup_command(root)
            self.assertIn('--supervise --root "'+root+'"',command)
            self.assertNotIn('%1',command)
            integration.register_startup(root,registry=registry)
            integration.register_startup(root,registry=registry)
            key=r'Software\Microsoft\Windows\CurrentVersion\Run'
            registry.values[key,'Contentrium CUT']='unknown executable'
            with self.assertRaises(CutError): integration.register_startup(root,registry=registry)
            self.assertEqual(registry.values[key,'Contentrium CUT'],'unknown executable')
            with self.assertRaises(CutError): integration.startup_command(str(Path(root)/('x'*230)))

    def test_windows_disabled_startup_is_diagnosed_without_reenabling(self):
        with tempfile.TemporaryDirectory() as root:
            registry=Registry()
            approved=r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
            registry.values[approved,'Contentrium CUT']=b'\x03'+b'\0'*11
            with self.assertRaises(CutError) as error:integration.register_startup(root,registry=registry)
            self.assertEqual(error.exception.code,'STARTUP_DISABLED')
            self.assertNotIn((r'Software\Microsoft\Windows\CurrentVersion\Run','Contentrium CUT'),registry.values)
            self.assertEqual(registry.values[approved,'Contentrium CUT'],b'\x03'+b'\0'*11)


if __name__=='__main__':unittest.main()
