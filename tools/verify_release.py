"""Anonymous production signature/download verification without touching live installation."""
import argparse,json,tempfile
from pathlib import Path
from contentrium_cut.updater import UpdateManager, API_ROOT, BINARY_HEADERS, _json
from contentrium_cut.windows_install import WindowsInstallation, _hash
ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--current',required=True);parser.add_argument('--download',action='store_true');args=parser.parse_args()
    config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='Contentrium-CUT-public-proof-') as directory:
        installation=WindowsInstallation(Path(directory)/'isolated-install')
        manager=UpdateManager(Path(directory)/'isolated-state',config['publicKey'],args.current,lambda e:None,installation)
        state=manager.check()
        if state['checkState'] not in ['AVAILABLE','CURRENT']:raise RuntimeError(json.dumps(state))
        release=_json(manager._get(API_ROOT+'/releases/latest').body)
        docs=[]
        for name in ['update-manifest.json','update-manifest.sig']:
            asset=next(a for a in release['assets'] if a['name']==name)
            docs.append(manager._get(asset['browser_download_url'],BINARY_HEADERS).body)
        manifest,reasons=manager._verify_manifest(*docs,release)
        if reasons:raise RuntimeError(str(reasons))
        verified=[]
        if args.download:
            for expected in manifest['assets']:
                actual=next(a for a in release['assets'] if a['id']==expected['assetId'])
                path=Path(directory)/expected['name']
                manager.transport.download(actual['browser_download_url'],path,dict(BINARY_HEADERS),lambda:None,expected['size'])
                if _hash(path)!=expected['sha256']:raise RuntimeError('Published asset hash differs')
                verified.append({'role':expected['role'],'size':expected['size'],'sha256':expected['sha256']})
        report={'releaseTitle':release['name'],'tag':release['tag_name'],'url':release['html_url'],
                'anonymousCheckState':state['checkState'],'currentVersion':args.current,
                'remoteVersion':manifest['appVersion'],'signatureVerified':True,'downloadedAssets':verified,
                'liveInstallationChanged':False}
        (ROOT/'docs/qa'/('release-'+manifest['appVersion']+'-proof.json')).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report))
if __name__=='__main__':main()
