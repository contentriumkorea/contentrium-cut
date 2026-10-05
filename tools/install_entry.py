"""Thin signed first-install GUI/CLI and stable native launcher; no AI imports."""
import argparse
import base64
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import re
import uuid
from contentrium_cut.updater import UpdateManager, API_ROOT, BINARY_HEADERS, SemVer, validate_zip, _json
from contentrium_cut.windows_install import WindowsInstallation, WindowsNamedMutex, _atomic_json, _guard_path, _hash
from contentrium_cut.contract import CutError
from contentrium_cut.dependencies import install_ffmpeg
from contentrium_cut.integration import install_integration, open_active

def release_url(tag=None):
    if tag is None: return API_ROOT + '/releases/latest'
    if not isinstance(tag, str) or not tag.startswith('v'):
        raise CutError('INSTALL_TAG', 'A stable v-prefixed release version is required.')
    try:
        version = SemVer(tag[1:])
        if version.pre: raise ValueError('Prerelease')
    except ValueError as error:
        raise CutError('INSTALL_TAG', 'A stable v-prefixed release version is required.') from error
    return API_ROOT + '/releases/tags/' + tag

def _release_metadata(document):
    return {key:document.get(key) for key in ('id','name','tag_name','draft','prerelease')} | {'assets':sorted(
        (asset['id'],asset['name'],asset['size'],asset.get('state'),asset['browser_download_url']) for asset in document['assets'])}

def _documents(manager, release):
    documents=[]
    for name in ('update-manifest.json','update-manifest.sig'):
        assets=[asset for asset in release['assets'] if asset['name']==name]
        if len(assets)!=1 or assets[0].get('state')!='uploaded':
            raise CutError('UPDATE_ASSET','Signed release documents are incomplete.')
        response=manager._get(assets[0]['browser_download_url'],BINARY_HEADERS)
        if response.status!=200: raise CutError('UPDATE_HTTP','Signed metadata download failed.')
        if len(response.body)!=assets[0]['size']: raise CutError('UPDATE_SIZE','Signed metadata size differs.')
        documents.append(response.body)
    return documents

def _confirm_selection(manager, release, documents):
    response=manager._get(API_ROOT + '/releases/' + str(release['id']))
    if response.status!=200: raise CutError('UPDATE_RELEASE','Selected release is no longer available.')
    fresh=_json(response.body)
    if _release_metadata(fresh)!=_release_metadata(release) or _documents(manager,fresh)!=documents:
        raise CutError('UPDATE_RELEASE','Selected release metadata changed before installation.')
    manager._verify_manifest(*documents,fresh)

def _integration_config(config, manifest):
    return dict(config,productId=manifest['productId'],appVersion=manifest['appVersion'],
                panelVersion=manifest['panelVersion'],companionVersion=manifest['companionVersion'],bundleId=manifest['bundleId'])

def setup_action(root):
    root=Path(root)
    journal=root/'updates'/'first-install.json'
    if journal.exists():
        try:
            if _json(journal.read_bytes()).get('state')!='READY_TO_OPEN': return 'repair'
        except Exception: return 'repair'
    return 'open' if (root/'app'/'active.json').exists() else 'install'

def install(config, root, tag=None, *, installation=None, transport=None, launcher_source=None,
            resource_root=None, dependency_installer=None, integration_installer=None,
            mutex=WindowsNamedMutex, progress=None):
    root = Path(root).resolve(); _guard_path(root)
    notify = progress or (lambda message: None)
    source = Path(launcher_source) if launcher_source else (Path(sys.executable) if getattr(sys, 'frozen', False) else None)
    if source is None or source.suffix.lower() != '.exe' or not source.is_file():
        raise CutError('LAUNCHER_SOURCE', 'A built Contentrium CUT Setup executable is required for the stable launcher.')
    _guard_path(source)
    with mutex(root):
        installation = installation or WindowsInstallation(root)
        journal_path=root/'updates'/'first-install.json'
        active=root/'app'/'active.json'
        _guard_path(journal_path); _guard_path(active)
        if active.exists() and not journal_path.exists():
            raise CutError('INSTALL_EXISTS','Contentrium CUT is installed. Open it and use its update action.')
        with tempfile.TemporaryDirectory(prefix='CUT-installer-') as scratch:
            manager = UpdateManager(Path(scratch) / 'verification', config['publicKey'], '0.0.0', lambda epoch: None,
                                    installation, transport=transport, signing_key_id=config.get('signingKeyId','contentrium-cut-2026-01'))
            if journal_path.exists():
                try:
                    journal=_json(journal_path.read_bytes())
                    if (type(journal.get('schemaVersion')) is not int or journal.get('schemaVersion')!=1 or journal.get('productId')!='com.contentrium.cut'
                            or journal.get('state') not in {'SELECTED','PREPARED','BOOTSTRAP_PENDING','INTEGRATION_PENDING','READY_TO_OPEN'}
                            or not re.fullmatch('first-install-[0-9a-f]{32}',journal.get('payloadDirectory',''))):
                        raise ValueError('Invalid installation journal')
                    documents=[base64.b64decode(journal[key],validate=True) for key in ('manifest','signature')]
                    release=journal['release']
                    if release.get('draft') is not False or release.get('prerelease') is not False or release.get('name')!='Contentrium CUT':
                        raise ValueError('Invalid saved stable release')
                    manifest,reasons=manager._verify_manifest(*documents,release)
                    if (journal.get('appVersion')!=manifest['appVersion'] or journal.get('bundleId')!=manifest['bundleId']
                            or (tag is not None and tag!=manifest['tag'])):
                        raise ValueError('Different recovery selection')
                    if journal['state'] in {'INTEGRATION_PENDING','READY_TO_OPEN'}:
                        receipt=journal.get('bootstrapResult',{})
                        if (receipt.get('status')!='PENDING_ACTIVATION' or receipt.get('appVersion')!=manifest['appVersion']
                                or receipt.get('bundleId')!=manifest['bundleId']):
                            raise ValueError('Missing bootstrap phase evidence')
                    if journal['state']=='READY_TO_OPEN' and not _integration_receipt(journal.get('integrationResult')):
                        raise ValueError('Missing integration phase evidence')
                except Exception as error:
                    raise CutError('INSTALL_RECOVERY','First-install recovery does not match this exact signed bundle.') from error
            else:
                installation._require_host_exit()
                notify('서명된 배포 정보를 확인하고 있습니다.')
                response=manager._get(release_url(tag))
                if response.status!=200: raise CutError('UPDATE_HTTP','Published Contentrium CUT release is unavailable.')
                release=_json(response.body)
                if (release.get('draft') is not False or release.get('prerelease') is not False
                        or release.get('name')!='Contentrium CUT' or (tag and release.get('tag_name')!=tag)):
                    raise CutError('UPDATE_RELEASE','Release is not the selected stable Contentrium CUT bundle.')
                documents=_documents(manager,release)
                manifest,reasons=manager._verify_manifest(*documents,release)
                if reasons: raise CutError('INSTALL_COMPATIBILITY','This PC is incompatible with the selected bundle.',reasons)
                journal={'schemaVersion':1,'productId':'com.contentrium.cut','state':'SELECTED',
                         'appVersion':manifest['appVersion'],'bundleId':manifest['bundleId'],
                         'release':release,'manifest':base64.b64encode(documents[0]).decode(),
                         'signature':base64.b64encode(documents[1]).decode(),
                         'payloadDirectory':'first-install-'+uuid.uuid4().hex}
                _atomic_json(journal_path,journal)  # Selected identity survives payload/host/installer failure.
            if reasons: raise CutError('INSTALL_COMPATIBILITY', 'This PC is incompatible with the selected bundle.', reasons)
            if active.exists():
                # Only the original exact verified bundle can repair integration, offline and without Adobe mutation.
                descriptor=_json(active.read_bytes())
                if (descriptor.get('appVersion')!=manifest['appVersion'] or descriptor.get('bundleId')!=manifest['bundleId']
                        or installation.verify_application(manifest['appVersion'],manifest['bundleId']) is not True):
                    raise CutError('INSTALL_EXISTS','Active installation differs from first-install recovery; use its update action.')
                result={'status':'PENDING_ACTIVATION','appVersion':manifest['appVersion'],'bundleId':manifest['bundleId'],
                        'versionDirectory':str(root/'app'/'versions'/manifest['appVersion']),'adobeRegistered':True}
                journal=dict(journal,state='INTEGRATION_PENDING',bootstrapResult=result)
                _atomic_json(journal_path,journal)
                integration=(integration_installer or install_integration)(root,_integration_config(config,manifest),
                    launcher_source=launcher_source,resource_root=resource_root)
                if not _integration_receipt(integration): raise CutError('INSTALL_INTEGRATION','Native launcher integration did not return a receipt.')
                _atomic_json(journal_path,dict(journal,state='READY_TO_OPEN',integrationResult=integration))
                return result
            installation._require_host_exit()
            # Recovery automatically uses its pinned release ID, never a later latest release.
            _confirm_selection(manager,release,documents)
            notify('검증된 프로그램 파일을 내려받고 있습니다.')
            cache=root/'updates'/journal['payloadDirectory']/'assets'
            _guard_path(cache); cache.mkdir(parents=True,exist_ok=True)
            prepared = {}
            for asset in manifest['assets']:
                actual = next(item for item in release['assets'] if item['id'] == asset['assetId'])
                path=cache/asset['name']; partial=path.with_name(path.name+'.part')
                _guard_path(path); _guard_path(partial)
                if not path.exists():
                    partial.unlink(missing_ok=True)  # Only this journal's named incomplete payload, never user data.
                    manager.transport.download(actual['browser_download_url'],partial,dict(BINARY_HEADERS),lambda:None,asset['size'])
                    if partial.stat().st_size!=asset['size'] or _hash(partial)!=asset['sha256']:
                        raise CutError('UPDATE_HASH','Download hash differs from signed manifest.')
                    os.replace(partial,path)
                if path.stat().st_size != asset['size'] or _hash(path) != asset['sha256']:
                    raise CutError('UPDATE_HASH', 'Download hash differs from signed manifest.')
                if asset['role'] in {'panel', 'companion'}: validate_zip(path)
                prepared[asset['role']] = {'path': str(path), 'sha256': asset['sha256'], 'assetId': asset['assetId']}
            journal=dict(journal,state='PREPARED',preparedAssets=prepared)
            _atomic_json(journal_path,journal)
            notify('제공자의 고정 FFmpeg 패키지를 설치하고 있습니다.')
            (dependency_installer or install_ffmpeg)(root)
            _confirm_selection(manager,release,documents)
            installation._require_host_exit()
            notify('Adobe 패널과 로컬 프로그램을 설치하고 있습니다.')
            journal=dict(journal,state='BOOTSTRAP_PENDING')
            _atomic_json(journal_path,journal)  # Durable before any Adobe command / app pointer mutation.
            result = installation.bootstrap(manifest, prepared)
            if (not isinstance(result,dict) or result.get('status')!='PENDING_ACTIVATION'
                    or result.get('appVersion')!=manifest['appVersion'] or result.get('bundleId')!=manifest['bundleId']):
                raise CutError('INSTALL_RESULT', 'Installation did not produce a pending activation receipt.')
            journal=dict(journal,state='INTEGRATION_PENDING',bootstrapResult=result)
            _atomic_json(journal_path,journal)
            integration=(integration_installer or install_integration)(root,_integration_config(config,manifest),
                launcher_source=launcher_source,resource_root=resource_root)
            if not _integration_receipt(integration): raise CutError('INSTALL_INTEGRATION','Native launcher integration did not return a receipt.')
            _atomic_json(journal_path,dict(journal,state='READY_TO_OPEN',integrationResult=integration))
            return result

def _integration_receipt(value):
    return isinstance(value,dict) and all(isinstance(value.get(key),str) and value[key] for key in ('launcher','shortcut'))

def show_gui(config, root, *, tag=None, launcher_source=None, resource_root=None):
    import tkinter as tk
    from tkinter import messagebox
    events = queue.Queue()
    window = tk.Tk(); window.title('Contentrium CUT Setup'); window.geometry('500x330'); window.resizable(False, False)
    window.configure(bg='#151515')
    tk.Label(window, text='CONTENTRIUM  CUT', bg='#151515', fg='#eeeeea', font=('Segoe UI',20)).pack(pady=(25,12))
    action=setup_action(root)
    status = tk.StringVar(value='진행 중이던 정확한 버전의 설치를 복구합니다.\n프로젝트를 저장하고 Premiere Pro 창을 닫아 주세요.' if action=='repair' else
                         '프로젝트를 저장하고 모든 Premiere Pro 창을 닫은 뒤 설치하세요.\n프로그램과 패널은 서명된 배포에서 설치됩니다.')
    tk.Label(window, textvariable=status, bg='#151515', fg='#bbbbbb', wraplength=440, justify='left').pack(padx=30,pady=15)
    running = [False]
    def launch():
        try: open_active(root); window.destroy()
        except Exception as error: messagebox.showerror('Contentrium CUT', getattr(error,'message','프로그램을 열 수 없습니다.'))
    def begin():
        running[0] = True; button.configure(state='disabled')
        def work():
            try:
                result = install(config,root,tag,launcher_source=launcher_source,resource_root=resource_root,
                                 progress=lambda message:events.put(('progress',message)))
                events.put(('success',result))
            except Exception as error: events.put(('error',getattr(error,'message','설치 중 오류가 발생했습니다.')))
        threading.Thread(target=work, name='Contentrium-CUT-Setup', daemon=False).start()
    button = tk.Button(window,text='Contentrium CUT 열기' if action=='open' else '설치 복구' if action=='repair' else '설치',
                       command=launch if action=='open' else begin,bg='#ddddda',fg='#151515',padx=25,pady=8)
    button.pack(pady=18)
    def poll():
        try:
            while True:
                kind,value = events.get_nowait()
                if kind == 'progress': status.set(value)
                elif kind == 'success':
                    running[0]=False;status.set('설치 준비가 완료됐습니다. Contentrium CUT을 열고\nPremiere Pro 패널에서 연결을 확인하세요.')
                    button.configure(text='Contentrium CUT 열기',state='normal',command=launch)
                else:
                    running[0]=False;status.set(value);button.configure(state='normal')
        except queue.Empty: pass
        window.after(150,poll)
    window.protocol('WM_DELETE_WINDOW',lambda:status.set('진행 중인 설치가 끝날 때까지 기다려 주세요.') if running[0] else window.destroy())
    poll(); window.mainloop()

def main(argv=None):
    parser=argparse.ArgumentParser()
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--install',action='store_true');mode.add_argument('--open',action='store_true')
    parser.add_argument('--tag');parser.add_argument('--config');parser.add_argument('--result');parser.add_argument('--root');parser.add_argument('--launcher-source')
    args, unknown=parser.parse_known_args(argv)
    if unknown and not args.open: parser.error('Unrecognized arguments')
    root=Path(args.root).resolve() if args.root else Path(os.environ['LOCALAPPDATA'])/'Contentrium CUT'
    resources=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))
    try:
        if args.open:
            open_active(root); return 0
        config_path=Path(args.config) if args.config else resources/'config.json'
        config=json.loads(config_path.read_text(encoding='utf-8-sig'))
        if not args.install:
            show_gui(config,root,tag=args.tag,launcher_source=args.launcher_source,resource_root=resources); return 0
        result=install(config,root,args.tag,launcher_source=args.launcher_source,resource_root=resources)
        if args.result: _atomic_json(Path(args.result).resolve(),result)
        if sys.stdout: print(json.dumps(result))
        return 0
    except Exception as error:
        failure={'error':{'code':getattr(error,'code','INSTALL_FAILED'),'message':getattr(error,'message','Contentrium CUT installation or launch failed.')}}
        if args.result: _atomic_json(Path(args.result).resolve(),failure)
        if args.install:
            if sys.stderr: print(failure['error']['code']+' '+failure['error']['message'],file=sys.stderr)
        else:
            import tkinter.messagebox as messagebox
            messagebox.showerror('Contentrium CUT',failure['error']['message'])
        return 1

if __name__ == '__main__': raise SystemExit(main())
