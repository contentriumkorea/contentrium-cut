"""Stable one-shot Launcher ownership and signed-byte replacement evidence."""
import base64
import ctypes
import json
import os
from pathlib import Path
import shutil
import uuid
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from .contract import CutError,canonical_hash

NAME='Contentrium CUT Launcher.exe'


def fail():raise CutError('LAUNCHER_OWNERSHIP','Stable launcher ownership or recovery evidence is invalid.')


def read(path):
    from .windows_install import _guard_path
    _guard_path(path)
    if path.stat().st_size>4*1024*1024:fail()
    return json.loads(path.read_text(encoding='utf-8'))


def signed(root,evidence):
    config=read(root/'launcher-config.json')
    try:
        raw=base64.b64decode(evidence['raw'],validate=True);wrapper=json.loads(base64.b64decode(evidence['signature'],validate=True))
        manifest=json.loads(raw);key_id=config.get('signingKeyId','contentrium-cut-2026-01')
        if wrapper['algorithm']!='Ed25519' or wrapper['keyId']!=key_id or manifest['signingKeyId']!=key_id or manifest['productId']!='com.contentrium.cut':fail()
        Ed25519PublicKey.from_public_bytes(base64.b64decode(config['publicKey'],validate=True)).verify(base64.b64decode(wrapper['signature'],validate=True),raw)
        installers=[a for a in manifest['assets'] if a['role']=='installer']
        if len(installers)!=1:fail()
        return manifest,installers[0]
    except Exception:fail()


def evidence_for(root,manifest=None):
    candidates=[]
    for filename in ['launcher-ownership.json','updates/first-install.json','updates/journal.json']:
        path=root/filename
        if not path.exists():continue
        value=read(path)
        if filename=='launcher-ownership.json':candidate=value.get('evidence')
        elif filename=='updates/first-install.json':candidate=dict(raw=value.get('manifest'),signature=value.get('signature'))
        else:candidate=value.get('_attemptCandidate')
        if candidate:candidates.append(candidate)
    for candidate in candidates:
        try:
            verified,asset=signed(root,candidate)
            if manifest is None or verified==manifest:return dict(raw=candidate['raw'],signature=candidate['signature']),verified,asset
        except CutError:continue
    fail()


def ownership(root):
    from .windows_install import _guard_path,_hash
    root=Path(root);launcher=root/NAME;config=root/'launcher-config.json'
    _guard_path(launcher);_guard_path(config)
    if not launcher.exists():
        if config.exists() or (root/'launcher-ownership.json').exists():fail()
        return {'present':False}
    if not config.is_file():fail()
    # Original 0.1.0 has no ownership receipt: authenticate its saved signed release.
    evidence,manifest,asset=evidence_for(root)
    if launcher.stat().st_size!=asset['size'] or _hash(launcher)!=asset['sha256']:fail()
    descriptor=read(config)
    if descriptor.get('appVersion')!=manifest['appVersion'] or descriptor.get('bundleId')!=manifest['bundleId']:fail()
    return dict(present=True,sha256=asset['sha256'],size=asset['size'],assetId=asset['assetId'],
                configHash=_hash(config),evidence=evidence,appVersion=manifest['appVersion'],bundleId=manifest['bundleId'])


def snapshot(root,backup):
    from .windows_install import _guard_path,_hash
    record=ownership(root);directory=backup/'launcher';directory.mkdir()
    names=[NAME,'launcher-config.json','launcher-ownership.json']
    files={}
    for name in names:
        source=root/name;_guard_path(source)
        if source.exists():shutil.copy2(source,directory/name);files[name]=_hash(directory/name)
    return dict(record,backup=str(directory),files=files)


def historical(root,record,snapshot):
    """A settled foreign attempt is evidence, never this snapshot's intent."""
    if record.get('state') not in {'PENDING_ACTIVATION','ROLLED_BACK'}:fail()
    current=ownership(root);prior=snapshot['launcher']
    if any(current.get(k)!=prior.get(k) for k in ['present','sha256','configHash']):fail()


def archive(root,record):
    from .windows_install import _atomic_json,_guard_path
    key=record.get('snapshotHash')
    if not isinstance(key,str) or len(key)!=64 or any(c not in '0123456789abcdef' for c in key):fail()
    path=root/'updates'/'launcher-history'/key/'launcher-transaction.json';_guard_path(path)
    if path.exists():
        if read(path)!=record:fail()
    else:_atomic_json(path,record)


def verify_snapshot(root,backup,record):
    from .windows_install import _guard_path,_hash
    directory=backup/'launcher';_guard_path(directory)
    if record.get('backup')!=str(directory) or set(record.get('files',{}))-{NAME,'launcher-config.json','launcher-ownership.json'}:fail()
    for name,digest in record['files'].items():
        _guard_path(directory/name)
        if _hash(directory/name)!=digest:fail()
    if record['present'] and record['files'].get(NAME)!=record['sha256']:fail()


def replace(source,destination,expected):
    from .windows_install import _guard_path,_hash,_kernel
    _guard_path(source);_guard_path(destination)
    if _hash(source)!=expected:fail()
    temporary=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.replace')
    try:
        with source.open('rb') as src,temporary.open('xb') as dst:
            shutil.copyfileobj(src,dst,1024*1024);dst.flush();os.fsync(dst.fileno())
        if _hash(temporary)!=expected:fail()
        if os.name=='nt':
            if not _kernel().MoveFileExW(str(temporary),str(destination),0x1|0x8):raise ctypes.WinError(ctypes.get_last_error())
        else:os.replace(temporary,destination)
        if _hash(destination)!=expected:fail()
    finally:temporary.unlink(missing_ok=True)


def restore(root,record,allowed_hash=None):
    from .windows_install import _guard_path,_hash
    directory=Path(record['backup']);launcher=root/NAME
    if launcher.exists() and _hash(launcher) not in {record.get('sha256'),allowed_hash}:fail()
    for name in [NAME,'launcher-config.json','launcher-ownership.json']:
        target=root/name;_guard_path(target)
        if name in record['files']:replace(directory/name,target,record['files'][name])
        elif target.exists():
            # Only a signed owned transaction may have created these absent files.
            if not allowed_hash:fail()
            target.unlink()
    if record['present'] and ownership(root)['sha256']!=record['sha256']:fail()


def required(root,version,bundle):
    """Absence is supported only for old headless-hook fixtures/legacy no-launcher installs."""
    root=Path(root);record=ownership(root)
    receipt=read(root/'app'/'versions'/version/'install.json')
    installers=[a for a in receipt.get('assets',[]) if a['role']=='installer']
    if installers:
        # During first-install bootstrap the stable launcher is provisioned next.
        if not record['present']:return not (root/'launcher-config.json').exists() and not (root/'updates'/'launcher-transaction.json').exists()
        if len(installers)!=1 or record['sha256']!=installers[0]['sha256'] or record['appVersion']!=version or record['bundleId']!=bundle:return False
    return True
