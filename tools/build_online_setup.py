"""Build a version-independent online entry with a hard download-size budget."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
MAX_BYTES=20*1024*1024


def main():
    config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    output=ROOT/'output'/config['appVersion']/'Contentrium-CUT-Install.exe'
    if output.exists():raise RuntimeError('Preserve existing online installer; use a new release version.')
    with tempfile.TemporaryDirectory(prefix='CUT-online-config-') as directory:
        data=Path(directory)/'online-config.json'
        data.write_text(json.dumps({key:config[key] for key in ['productId','publicKey','signingKeyId']}),encoding='utf-8')
        args=[sys.executable,'-m','PyInstaller','--noconfirm','--onefile','--windowed','--name','Contentrium-CUT-Install',
              '--workpath',str(ROOT/'build/online'),'--paths',str(ROOT/'companion'),'--add-data',str(data)+';.',
              '--add-data',str(ROOT/'licenses')+';licenses']
        # Downloader must not pull the install/analysis graph through lazy imports
        # in the shared updater. Only trust validation and transport are used here.
        for path in (ROOT/'companion/contentrium_cut').glob('*.py'):
            if path.stem not in {'updater','contract','online_installer'}:args+=['--exclude-module','contentrium_cut.'+path.stem]
        for package in ['numpy','scipy','torch','torchaudio','onnxruntime','pyannote','matplotlib','pandas','PIL','tkinter.test']:
            args+=['--exclude-module',package]
        subprocess.run(args+[str(ROOT/'tools/online_install_entry.py')],cwd=ROOT,check=True)
    built=ROOT/'dist/Contentrium-CUT-Install.exe'
    if not 0<built.stat().st_size<=MAX_BYTES:raise RuntimeError('Online installer exceeds 20MiB budget; do not publish.')
    output.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(built,output)
    print(json.dumps(dict(path=str(output),size=output.stat().st_size,maxBytes=MAX_BYTES)))


if __name__=='__main__':main()
