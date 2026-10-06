"""Reproducible local runtime/CCX packaging, with no development harness or keys."""
import argparse,json,shutil,subprocess,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
parser=argparse.ArgumentParser()
parser.add_argument('--silero-dir',required=True,help='Explicit verified Silero model directory; never infer the install root from the build process environment.')
options=parser.parse_args()
model=Path(options.silero_dir).resolve(strict=True)
if not model.is_dir() or not (model/'manifest.json').is_file():raise SystemExit('A verified installed Silero directory is required.')
sys.path.insert(0,str(ROOT/'companion'))
from contentrium_cut.models import ModelManager
from contentrium_cut.release_assets import assert_public_tree
assert_public_tree(ROOT/'config.json')
assert_public_tree(ROOT/'plugin')
if model.name!='silero' or ModelManager(model.parent).state('silero')['status']!='ready':
    raise SystemExit('Silero model manifest and file hashes must pass verification before packaging.')
out=ROOT/'output'/config['appVersion']
if any((out/name).exists() for name in ['Contentrium-CUT-Windows-x64.zip','Contentrium-CUT.ccx']):
    raise SystemExit('Release payload already exists. Preserve it and build a new product version.')
config['productId']='com.contentrium.cut'
for file in [ROOT/'config.json',ROOT/'plugin'/'bundle.json']:file.write_text(json.dumps(config,indent=2)+'\n',encoding='utf-8')
manifest=json.loads((ROOT/'plugin'/'manifest.json').read_text());manifest['version']=config['appVersion']
(ROOT/'plugin'/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
args=[sys.executable,'-m','PyInstaller','--noconfirm','--onedir','--windowed','--name','Contentrium CUT','--paths',str(ROOT/'companion'),'--add-data',str(ROOT/'config.json')+';.', '--add-data',str(model)+';silero']
args+=['--add-data',str(ROOT/'licenses')+';licenses']
for package in ['numpy','scipy','onnxruntime','cryptography','torch','torchaudio','pyannote.audio','pyannote.core','pyannote.database','pyannote.metrics','huggingface_hub']:
    args+=['--collect-all',package]
for package in ['torchcodec','torchmetrics','lightning','pytorch_lightning','pytorch_metric_learning','speechbrain','safetensors','einops','yaml','soundfile','rich','requests','filelock','packaging','tqdm','matplotlib','sklearn','pandas','asteroid_filterbanks']:
    args+=['--collect-submodules',package]
args+=[str(ROOT/'companion'/'main.py')]
subprocess.run(args,cwd=ROOT,check=True)
out.mkdir(parents=True,exist_ok=True)
runtime=ROOT/'dist'/'Contentrium CUT'
shutil.copy2(ROOT/'config.json',runtime/'config.json')
assert_public_tree(runtime)
assert_public_tree(ROOT/'plugin')
with zipfile.ZipFile(out/'Contentrium-CUT-Windows-x64.zip','w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for path in sorted(runtime.rglob('*')):
        if path.is_file():z.write(path,path.relative_to(runtime).as_posix())
with zipfile.ZipFile(out/'Contentrium-CUT.ccx','w',zipfile.ZIP_DEFLATED) as z:
    for path in sorted((ROOT/'plugin').rglob('*')):
        if path.is_file():z.write(path,path.relative_to(ROOT/'plugin').as_posix())
print('Runtime and CCX packaged: '+config['appVersion'])
