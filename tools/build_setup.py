import json,shutil,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--onefile','--windowed','--name','Contentrium-CUT-Setup','--paths',str(root/'companion'),'--add-data',str(root/'config.json')+';.','--add-data',str(root/'licenses')+';licenses',str(root/'tools/install_entry.py')],cwd=root,check=True)
version=json.loads((root/'config.json').read_text())['appVersion'];out=root/'output'/version;out.mkdir(parents=True,exist_ok=True)
shutil.copy2(root/'dist/Contentrium-CUT-Setup.exe',out/'Contentrium-CUT-Setup.exe')
print('Standalone setup packaged: '+version)
