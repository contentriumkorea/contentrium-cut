"""Publish one reviewed bundle, signing the actual GitHub asset identities."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_pem_private_key

ROOT = Path(__file__).resolve().parents[1]
GH = r'C:\Program Files\GitHub CLI\gh.exe'
REPO = 'contentriumkorea/contentrium-cut'

def gh(*args, capture=False):
    result = subprocess.run([GH, *args], cwd=ROOT, check=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout if capture else None

def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()

def selected_release(tag):
    # Drafts are listed for the authenticated publisher; GitHub's tag endpoint
    # does not resolve a tag that has not yet been created by publication.
    matches=[r for r in json.loads(gh('api',f'repos/{REPO}/releases',capture=True)) if r['tag_name']==tag]
    if len(matches)>1:raise RuntimeError('Ambiguous release identity')
    return matches[0] if matches else None

def required_release(tag):
    # A freshly created draft can briefly be absent from GitHub's list API.
    # Read again without recreating the draft or replacing uploaded assets.
    for attempt in range(6):
        release=selected_release(tag)
        if release is not None:return release
        if attempt<5:time.sleep(min(0.5*2**attempt,2))
    raise RuntimeError('GitHub release is not yet visible; retry the same publication without replacing its draft.')

def upload_missing(tag,paths):
    release=required_release(tag)
    missing=[]
    for path in paths:
        matches=[a for a in release['assets'] if a['name']==path.name]
        if not matches:missing.append(str(path));continue
        actual=matches[0]
        if len(matches)!=1 or actual['size']!=path.stat().st_size or actual.get('digest')!='sha256:'+digest(path):
            raise RuntimeError('Existing draft asset differs; refuse replacement: '+path.name)
    if missing:gh('release','upload',tag,'--repo',REPO,*missing)
    verified=required_release(tag)
    if not verified or verified['id']!=release['id'] or not verified['draft']:
        raise RuntimeError('Draft identity changed during upload')
    for path in paths:
        matches=[a for a in verified['assets'] if a['name']==path.name]
        if (len(matches)!=1 or matches[0]['state']!='uploaded' or matches[0]['size']!=path.stat().st_size
                or matches[0].get('digest')!='sha256:'+digest(path)):
            raise RuntimeError('Uploaded asset verification failed: '+path.name)
    return verified

def signed_documents(output, fields, key):
    """Retain exact staged bytes when resuming an interrupted draft upload."""
    manifest_path, signature_path = output/'update-manifest.json', output/'update-manifest.sig'
    if signature_path.exists() and not manifest_path.exists():
        raise RuntimeError('Staged manifest is missing; preserve the existing signature for recovery')
    if manifest_path.exists():
        raw = manifest_path.read_bytes()
        try:
            saved = json.loads(raw)
            built_at = datetime.fromisoformat(saved.pop('builtAt'))
            if (json.dumps(saved,sort_keys=True,allow_nan=False) != json.dumps(fields,sort_keys=True,allow_nan=False)
                    or built_at.tzinfo is None):
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise RuntimeError('Staged manifest differs from the reviewed draft; refuse replacement') from None
    else:
        raw = (json.dumps(dict(fields, builtAt=datetime.now(timezone.utc).isoformat()),
                          ensure_ascii=False, separators=(',', ':'))+'\n').encode('utf-8')
    signature = dict(algorithm='Ed25519', keyId=fields['signingKeyId'],
                     signature=base64.b64encode(key.sign(raw)).decode('ascii'))
    signature_raw = json.dumps(signature).encode('utf-8')
    if signature_path.exists():
        try:
            signature_raw = signature_path.read_bytes()
            saved_signature = json.loads(signature_raw)
            if set(saved_signature) != set(signature) or saved_signature['algorithm'] != 'Ed25519' or saved_signature['keyId'] != fields['signingKeyId']:
                raise ValueError()
            key.public_key().verify(base64.b64decode(saved_signature['signature'], validate=True), raw)
        except (ValueError, TypeError, KeyError, InvalidSignature):
            raise RuntimeError('Staged signature is invalid; refuse replacement') from None
    # Exclusive creation never replaces a staged or concurrently created document.
    for path, data in [(manifest_path, raw), (signature_path, signature_raw)]:
        if not path.exists():
            try:
                with path.open('xb') as destination:
                    destination.write(data); destination.flush(); os.fsync(destination.fileno())
            except FileExistsError:
                pass
        if path.read_bytes() != data:
            raise RuntimeError('Staged document changed concurrently; preserve it for recovery')
    return [manifest_path, signature_path]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--notes', required=True)
    parser.add_argument('--target', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-fA-F]{40}', args.target):
        raise RuntimeError('Reviewed target must be a full 40-character commit SHA')
    config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    version = config['appVersion']; tag = 'v' + version
    output = ROOT / 'output' / version
    files = [('companion', 'Contentrium-CUT-Windows-x64.zip'),
             ('panel', 'Contentrium-CUT.ccx'), ('installer', 'Contentrium-CUT-Setup.exe')]
    for _, name in files:
        if not (output / name).is_file():
            raise RuntimeError('Build is missing: ' + name)
    release=selected_release(tag)
    if release:
        if not release['draft'] or release['name']!='Contentrium CUT' or release['target_commitish']!=args.target:
            raise RuntimeError('Existing release is not this unpublished reviewed target')
    else:
        gh('release','create',tag,'--repo',REPO,'--draft','--title','Contentrium CUT',
           '--target',args.target,'--notes-file',str(Path(args.notes).resolve()))
    upload_missing(tag,[output/name for _,name in files])
    release=required_release(tag)
    assets = []
    for role, name in files:
        actual = next(a for a in release['assets'] if a['name'] == name)
        size = (output / name).stat().st_size
        if actual['size'] != size or actual['state'] != 'uploaded' or actual.get('digest') != 'sha256:'+digest(output/name):
            raise RuntimeError('GitHub upload is incomplete or its digest differs')
        assets.append(dict(role=role, name=name, assetId=actual['id'], size=size,
                           sha256=digest(output / name)))
    notes = Path(args.notes).read_text(encoding='utf-8')
    manifest = dict(schemaVersion=1, productId=config['productId'], displayName='Contentrium CUT',
                    appVersion=version, channel='stable', releaseId=release['id'], tag=tag,
                    platform='windows', architecture='x64',
                    panelVersion=version, companionVersion=version, bundleId=config['bundleId'],
                    updateProtocolRange={'min': 1, 'max': 1}, minUpdaterVersion='0.1.0',
                    dataSchemaFrom=[1], dataSchemaTo=1, migrationId='none', assets=assets,
                    modelCompatibility={'silero': 'retained', 'community-1': 'user-authorized-local-model'},
                    releaseNotes=notes, signingKeyId=config['signingKeyId'])
    key_path = Path(os.environ['LOCALAPPDATA']) / 'Contentrium CUT Publisher' / 'ed25519.pem'
    key = load_pem_private_key(key_path.read_bytes(), password=None)
    if base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode('ascii') != config['publicKey']:
        raise RuntimeError('Publisher key does not match the configured public key')
    verified = upload_missing(tag, signed_documents(output, manifest, key))
    if verified['name']!='Contentrium CUT' or verified['target_commitish']!=args.target:
        raise RuntimeError('Reviewed draft target changed before publication')
    gh('release', 'edit', tag, '--repo', REPO, '--draft=false', '--latest')
    print('Published ' + required_release(tag)['html_url'])

if __name__ == '__main__':
    main()
