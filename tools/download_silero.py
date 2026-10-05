"""Explicit pinned official model setup; no recording is uploaded."""
import hashlib
import json
import os
from pathlib import Path
from urllib.request import urlopen

REVISION = '1e261b036686cd0017d500ee96acd1c4ba572a9d'
URL = f'https://raw.githubusercontent.com/snakers4/silero-vad/{REVISION}/src/silero_vad/data/silero_vad.onnx'
root = Path(os.environ['LOCALAPPDATA']) / 'Contentrium CUT' / 'models' / 'silero'
root.mkdir(parents=True, exist_ok=True)
with urlopen(URL, timeout=30) as response:
    data = response.read(20_000_001)
if not data or len(data)>20_000_000:
    raise RuntimeError('Invalid official model size')
digest = hashlib.sha256(data).hexdigest()
(root / 'silero_vad.onnx').write_bytes(data)
(root / 'manifest.json').write_text(json.dumps({'modelId':'silero','revision':REVISION,'source':URL,'entrypoint':'silero_vad.onnx','files':{'silero_vad.onnx':digest}},indent=2),encoding='utf-8')
print(json.dumps({'path':str(root),'sha256':digest,'size':len(data)}))
