"""Reproducible local runtime/CCX packaging, with no development harness or keys."""
import json,os,shutil,subprocess,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
config['productId']='com.contentrium.cut'
for file in [ROOT/'config.json',ROOT/'plugin'/'bundle.json']:file.write_text(json.dumps(config,indent=2)+'\n',encoding='utf-8')
manifest=json.loads((ROOT/'plugin'/'manifest.json').read_text());manifest['version']=config['appVersion']
(ROOT/'plugin'/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
model=Path(os.environ['LOCALAPPDATA'])/'Contentrium CUT'/'models'/'silero'
args=[sys.executable,'-m','PyInstaller','--noconfirm','--onedir','--windowed','--name','Contentrium CUT','--paths',str(ROOT/'companion'),'--add-data',str(ROOT/'config.json')+';.', '--add-data',str(model)+';silero']
args+=['--add-data',str(ROOT/'licenses')+';licenses']
for package in ['numpy','scipy','onnxruntime','cryptography','torch','torchaudio','pyannote.audio','pyannote.core','pyannote.database','pyannote.metrics','huggingface_hub']:
    args+=['--collect-all',package]
for package in ['torchcodec','torchmetrics','lightning','pytorch_lightning','pytorch_metric_learning','speechbrain','safetensors','einops','yaml','soundfile','rich','requests','filelock','packaging','tqdm','matplotlib','sklearn','pandas','asteroid_filterbanks']:
    args+=['--collect-submodules',package]
args+=[str(ROOT/'companion'/'main.py')]
subprocess.run(args,cwd=ROOT,check=True)
out=ROOT/'output'/config['appVersion'];out.mkdir(parents=True,exist_ok=True)
runtime=ROOT/'dist'/'Contentrium CUT'
shutil.copy2(ROOT/'config.json',runtime/'config.json')
with zipfile.ZipFile(out/'Contentrium-CUT-Windows-x64.zip','w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for path in sorted(runtime.rglob('*')):
        if path.is_file():z.write(path,path.relative_to(runtime).as_posix())
with zipfile.ZipFile(out/'Contentrium-CUT.ccx','w',zipfile.ZIP_DEFLATED) as z:
    for path in sorted((ROOT/'plugin').rglob('*')):
        if path.is_file():z.write(path,path.relative_to(ROOT/'plugin').as_posix())
print('Runtime and CCX packaged: '+config['appVersion'])
