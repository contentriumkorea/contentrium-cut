"""Generate once outside the repository. Never print or package private material."""
import base64
import json
import os
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,PublicFormat,NoEncryption,load_pem_private_key

root=Path(os.environ['LOCALAPPDATA'])/'Contentrium CUT Publisher'
root.mkdir(exist_ok=True)
path=root/'ed25519.pem'
if not path.exists():path.write_bytes(Ed25519PrivateKey.generate().private_bytes(Encoding.PEM,PrivateFormat.PKCS8,NoEncryption()))
key=load_pem_private_key(path.read_bytes(),None)
public=base64.b64encode(key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode()
project=Path(__file__).resolve().parents[1]
config_path=project/'config.json'
if config_path.is_file():
    config=json.loads(config_path.read_text(encoding='utf-8'))
    if config.get('publicKey')!=public:raise RuntimeError('Existing public key differs. Explicit key rotation is required.')
else:
    config={'productId':'com.contentrium.cut','displayName':'Contentrium CUT','appVersion':'0.1.0','panelVersion':'0.1.0','companionVersion':'0.1.0','bundleId':'contentrium-cut-0.1.0','dataSchemaVersion':1,'protocolVersion':1,'publicKey':public,'signingKeyId':'contentrium-cut-2026-01'}
(project/'config.json').write_text(json.dumps(config,indent=2)+'\n',encoding='utf-8')
(project/'plugin'/'bundle.json').write_text(json.dumps(config,indent=2)+'\n',encoding='utf-8')
print('Public configuration written; signing key retained outside the repository.')
