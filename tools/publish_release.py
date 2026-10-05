"""Publish one reviewed bundle, signing the actual GitHub asset identities."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from cryptography.hazmat.primitives.serialization import load_pem_private_key

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

def upload_missing(tag,paths):
    release=selected_release(tag)
    missing=[]
    for path in paths:
        matches=[a for a in release['assets'] if a['name']==path.name]
        if not matches:missing.append(str(path));continue
        actual=matches[0]
        if len(matches)!=1 or actual['size']!=path.stat().st_size or actual.get('digest')!='sha256:'+digest(path):
            raise RuntimeError('Existing draft asset differs; refuse replacement: '+path.name)
    if missing:gh('release','upload',tag,'--repo',REPO,*missing)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--notes', required=True)
    parser.add_argument('--target', required=True)
    args = parser.parse_args()
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
    release=selected_release(tag)
    assets = []
    for role, name in files:
        actual = next(a for a in release['assets'] if a['name'] == name)
        size = (output / name).stat().st_size
        if actual['size'] != size or actual['state'] != 'uploaded':
            raise RuntimeError('GitHub upload is incomplete')
        assets.append(dict(role=role, name=name, assetId=actual['id'], size=size,
                           sha256=digest(output / name)))
    notes = Path(args.notes).read_text(encoding='utf-8')
    manifest = dict(schemaVersion=1, productId=config['productId'], displayName='Contentrium CUT',
                    appVersion=version, channel='stable', releaseId=release['id'], tag=tag,
                    builtAt=datetime.now(timezone.utc).isoformat(), platform='windows', architecture='x64',
                    panelVersion=version, companionVersion=version, bundleId=config['bundleId'],
                    updateProtocolRange={'min': 1, 'max': 1}, minUpdaterVersion='0.1.0',
                    dataSchemaFrom=[1], dataSchemaTo=1, migrationId='none', assets=assets,
                    modelCompatibility={'silero': 'retained', 'community-1': 'user-authorized-local-model'},
                    releaseNotes=notes, signingKeyId=config['signingKeyId'])
    raw = (json.dumps(manifest, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    key_path = Path(os.environ['LOCALAPPDATA']) / 'Contentrium CUT Publisher' / 'ed25519.pem'
    key = load_pem_private_key(key_path.read_bytes(), password=None)
    signature = dict(algorithm='Ed25519', keyId=config['signingKeyId'],
                     signature=base64.b64encode(key.sign(raw)).decode('ascii'))
    (output / 'update-manifest.json').write_bytes(raw)
    (output / 'update-manifest.sig').write_text(json.dumps(signature), encoding='utf-8')
    upload_missing(tag,[output/'update-manifest.json',output/'update-manifest.sig'])
    gh('release', 'edit', tag, '--repo', REPO, '--draft=false', '--latest')
    print('Published ' + selected_release(tag)['html_url'])

if __name__ == '__main__':
    main()
