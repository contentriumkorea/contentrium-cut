import json,shutil,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'companion'))
from contentrium_cut.release_assets import assert_public_tree
assert_public_tree(root/'config.json')
assert_public_tree(root/'licenses')
version=json.loads((root/'config.json').read_text(encoding='utf-8'))['appVersion'];out=root/'output'/version
if (out/'Contentrium-CUT-Setup.exe').exists():raise SystemExit('Release Setup already exists. Preserve it and build a new product version.')
# Setup verifies model manifests; inference runs only in the separate signed
# runtime. Avoid embedding its AI engine through lazy imports in shared helpers.
args=[sys.executable,'-m','PyInstaller','--noconfirm','--onefile','--windowed','--name','Contentrium-CUT-Setup','--paths',str(root/'companion'),'--add-data',str(root/'config.json')+';.','--add-data',str(root/'licenses')+';licenses']
for module in ['contentrium_cut.service','torch','torchaudio','onnxruntime','pyannote','huggingface_hub']:
    args+=['--exclude-module',module]
subprocess.run(args+[str(root/'tools/install_entry.py')],cwd=root,check=True)
out.mkdir(parents=True,exist_ok=True)
shutil.copy2(root/'dist/Contentrium-CUT-Setup.exe',out/'Contentrium-CUT-Setup.exe')
print('Standalone setup packaged: '+version)
