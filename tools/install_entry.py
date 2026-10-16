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
from contentrium_cut.integration import install_integration, open_active, resolve_setup_root
from contentrium_cut.bootstrap import known_folder, canonical_root
from contentrium_cut.installer_preparation import prepare_installation
from contentrium_cut.installer_reporting import failure,write_report

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
            mutex=WindowsNamedMutex, progress=None, mapping_evidence=None, preparation=None, cancel=None, wait=None, transfer_progress=None):
    root = canonical_root(root)
    def integrate(manifest):
        nonlocal journal
        target = _integration_config(config,manifest)
        read_grace = 0
        while True:
            try:
                prepared = (preparation or prepare_installation)(root,target,installation=installation,mapping_evidence=mapping_evidence)
            except CutError as error:
                if error.code!='INSTALL_RECEIPT_INCOMPLETE':raise
                if wait is None or read_grace >= 3:
                    raise CutError('INSTALL_RECEIPT_INVALID','패널의 설치 확인 기록이 불완전합니다. 같은 Setup으로 설치 복구가 필요합니다.') from None
                read_grace += 1
                if wait(0.1):return dict(status='PENDING_PROVISIONING',appVersion=manifest['appVersion'],bundleId=manifest['bundleId'],canceled=True)
                continue
            if prepared.get('status') == 'PREPARED': break
            if prepared.get('status') != 'PENDING_PROVISIONING':
                raise CutError('INSTALL_PREPARATION','Installation preparation did not return a verified result.')
            notify('Premiere Pro에서 Contentrium CUT 패널을 열어 주세요. 설치 연결을 준비하고 있습니다.')
            if wait is None or (cancel and cancel()): return prepared
            if wait(0.75): return dict(prepared,canceled=True)
        check_cancel()
        journal=dict(journal,state='INTEGRATION_PENDING',preparationResult=prepared)
        _atomic_json(journal_path,journal)
        options = dict(launcher_source=launcher_source,resource_root=resource_root,
                       mapping_evidence=prepared.get('mappingEvidence',mapping_evidence))
        result = (integration_installer or install_integration)(root,target,**options)
        return result
    notify = progress or (lambda message: None)
    def check_cancel():
        if cancel and cancel(): raise CutError('CANCELED','설치 진행을 중단했습니다. 같은 Setup으로 이어서 준비할 수 있습니다.')
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
                            or journal.get('state') not in {'SELECTED','PREPARED','BOOTSTRAP_PENDING','PROVISIONING_PENDING','INTEGRATION_PENDING','READY_TO_OPEN'}
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
                    if journal['state'] in {'PROVISIONING_PENDING','INTEGRATION_PENDING','READY_TO_OPEN'}:
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
                journal=dict(journal,state='PROVISIONING_PENDING',bootstrapResult=result)
                _atomic_json(journal_path,journal)
                integration=integrate(manifest)
                if integration.get('status')=='PENDING_PROVISIONING': return integration
                if not _integration_receipt(integration): raise CutError('INSTALL_INTEGRATION','Native launcher integration did not return a receipt.')
                _atomic_json(journal_path,dict(journal,state='READY_TO_OPEN',integrationResult=integration))
                return result
            installation._require_host_exit()
            check_cancel()
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
                    options={'progress':lambda received,total,name=asset['name']:transfer_progress(name,received,total)} if transfer_progress else {}
                    manager.transport.download(actual['browser_download_url'],partial,dict(BINARY_HEADERS),check_cancel,asset['size'],**options)
                    if partial.stat().st_size!=asset['size'] or _hash(partial)!=asset['sha256']:
                        raise CutError('UPDATE_HASH','Download hash differs from signed manifest.')
                    os.replace(partial,path)
                if path.stat().st_size != asset['size'] or _hash(path) != asset['sha256']:
                    raise CutError('UPDATE_HASH', 'Download hash differs from signed manifest.')
                if asset['role'] in {'panel', 'companion'}: validate_zip(path)
                prepared[asset['role']] = {'path': str(path), 'sha256': asset['sha256'], 'assetId': asset['assetId']}
            journal=dict(journal,state='PREPARED',preparedAssets=prepared)
            _atomic_json(journal_path,journal)
            check_cancel()
            notify('제공자의 고정 FFmpeg 패키지를 설치하고 있습니다.')
            if dependency_installer:dependency_installer(root)
            else:install_ffmpeg(root,cancel=cancel,progress=(lambda received,total:transfer_progress('FFmpeg 제공자 패키지',received,total)) if transfer_progress else None)
            _confirm_selection(manager,release,documents)
            installation._require_host_exit()
            check_cancel()
            notify('Adobe 패널과 로컬 프로그램을 설치하고 있습니다.')
            journal=dict(journal,state='BOOTSTRAP_PENDING')
            _atomic_json(journal_path,journal)  # Durable before any Adobe command / app pointer mutation.
            result = installation.bootstrap(manifest, prepared)
            if (not isinstance(result,dict) or result.get('status')!='PENDING_ACTIVATION'
                    or result.get('appVersion')!=manifest['appVersion'] or result.get('bundleId')!=manifest['bundleId']):
                raise CutError('INSTALL_RESULT', 'Installation did not produce a pending activation receipt.')
            journal=dict(journal,state='PROVISIONING_PENDING',bootstrapResult=result)
            _atomic_json(journal_path,journal)
            integration=integrate(manifest)
            if integration.get('status')=='PENDING_PROVISIONING': return integration
            if not _integration_receipt(integration): raise CutError('INSTALL_INTEGRATION','Native launcher integration did not return a receipt.')
            _atomic_json(journal_path,dict(journal,state='READY_TO_OPEN',integrationResult=integration))
            return result

def _integration_receipt(value):
    return isinstance(value,dict) and all(isinstance(value.get(key),str) and value[key] for key in ('launcher','shortcut'))

def run_setup_worker(config, root, events, cancel_event, **options):
    """Tk-free worker boundary; waiting/cancel never blocks the Setup event loop."""
    try:
        result = install(config,root,progress=lambda message:events.put(('progress',message)),
                         cancel=cancel_event.is_set,wait=cancel_event.wait,
                         transfer_progress=lambda name,received,total:events.put(('transfer',dict(name=name,received=received,total=total))),**options)
        if result.get('canceled'):raise CutError('CANCELED','패널 연결 준비를 중단했습니다. 보존된 준비 상태에서 다시 이어갈 수 있습니다.')
        events.put(('pending' if result.get('status')=='PENDING_PROVISIONING' else 'success',result))
    except Exception as error:
        events.put(('error',failure(error,'install',version=config.get('appVersion'))))

def run_root_worker(config,events,cancel_event,*,explicit_root=None):
    try:
        root=resolve_setup_root(config,explicit=explicit_root)
        if cancel_event.is_set():raise CutError('CANCELED','설치 위치 확인 후 중단했습니다. 설치 파일은 변경하지 않았습니다.')
        events.put(('resolved',root))
    except Exception as error:events.put(('error',failure(error,'location',version=config.get('appVersion'))))


def show_gui(config, root, *, tag=None, launcher_source=None, resource_root=None, result_path=None,explicit_root=None):
    from contentrium_cut.installer_ui import InstallerWindow
    from contentrium_cut.installer_reporting import save_diagnostic
    view=InstallerWindow('프로그램 설치 및 연결');window=view.window
    events=queue.Queue();cancel_event=threading.Event();running=[False];closing=[False]
    version=config.get('appVersion');outcome=failure(CutError('CANCELED','설치를 시작하지 않고 닫았습니다.'),'not-started',version=version)
    action=setup_action(root) if root is not None else None
    def record(value):
        outcome.clear();outcome.update(value)
        if result_path:write_report(result_path,outcome)
    def failed(report):
        record(report)
        try:log=save_diagnostic(report)
        except (OSError,ValueError):log=None
        view.failure(report,log)
        view.primary_button.configure(command=launch if action=='open' else begin)
    def launch():
        try:
            open_active(root);record(dict(schemaVersion=1,status='OPENED',appVersion=version,phase='open-existing'));window.destroy()
        except Exception as error:failed(failure(error,'open-existing',version=version))
    def begin():
        running[0]=True;closing[0]=False;cancel_event.clear();view.error_text.set('');view.copy_button.configure(state='disabled')
        view.primary_button.configure(state='disabled');view.cancel_button.configure(text='취소',state='normal')
        if root is None:
            view.phase(1,'설치 위치와 기존 파일을 확인합니다','기존 설치의 서명과 파일 해시를 확인합니다. 파일 수에 따라 시간이 걸릴 수 있습니다.')
            threading.Thread(target=run_root_worker,args=(config,events,cancel_event),kwargs=dict(explicit_root=explicit_root),name='CUT-install-location',daemon=False).start();return
        view.phase(1,'설치 환경을 확인합니다','프로젝트를 저장하고 모든 Premiere Pro 창을 정상 종료해 주세요.')
        threading.Thread(target=run_setup_worker,args=(config,root,events,cancel_event),
            kwargs=dict(tag=tag,launcher_source=launcher_source,resource_root=resource_root),name='Contentrium-CUT-Setup',daemon=False).start()
    def close():
        if running[0]:
            closing[0]=True;cancel_event.set();view.phase(4,'안전하게 중단하고 있습니다','현재 작업 경계에서 중단합니다. 기존 설치와 자료는 보존합니다.');view.cancel_button.configure(state='disabled');return
        if result_path and not Path(result_path).exists():write_report(result_path,outcome)
        window.destroy()
    def initial_state():
        view.primary_button.configure(text='Contentrium CUT 열기' if action=='open' else '설치 복구' if action=='repair' else '설치 시작',command=launch if action=='open' else begin,state='normal')
        view.cancel_button.configure(text='닫기',state='normal')
        view.ready('기존 설치를 확인했습니다' if action=='open' else '설치 연결을 이어갑니다' if action=='repair' else 'Contentrium CUT을 설치합니다',
                   '기존 패널의 업데이트로 새 버전을 설치할 수 있습니다.' if action=='open' else '중단된 설치 준비와 실패 기록을 보존해 이어갑니다.' if action=='repair' else '서명된 본체와 Adobe 패널을 내려받습니다. Premiere는 정상 종료한 상태에서 설치하세요.')
    initial_state()
    view.cancel_button.configure(command=close)
    def poll():
        nonlocal root,action
        try:
            while True:
                kind,value=events.get_nowait()
                if kind=='resolved':
                    running[0]=False;root=value;action=setup_action(root)
                    if closing[0]:window.destroy();return
                    initial_state()
                elif kind=='transfer':view.transfer(value['name'],value['received'],value['total'])
                elif kind=='progress':
                    step=4 if 'Adobe' in value or '패널을 열어' in value else 2 if '내려받' in value or 'FFmpeg' in value else 3 if '검증' in value else 1
                    view.phase(step,'패널 연결 대기' if '패널을 열어' in value else '설치 파일을 준비하고 있습니다',value)
                elif kind=='error':
                    running[0]=False;failed(value);view.cancel_button.configure(text='닫기',state='normal')
                    if closing[0]:window.destroy();return
                elif kind in {'success','pending'}:
                    running[0]=False
                    status='PENDING_PROVISIONING' if kind=='pending' else 'READY_TO_OPEN'
                    record(dict(schemaVersion=1,status=status,appVersion=value.get('appVersion',version),phase='panel-connection'))
                    view.ready('Premiere 패널 연결을 준비해 주세요','파일 준비를 마쳤습니다. Premiere에서 Window → UXP Plugins → Contentrium CUT 패널을 열어 첫 연결을 마무리하세요.')
                    view.primary_button.configure(text='연결 준비 계속' if kind=='pending' else 'Contentrium CUT 열기',state='normal',command=begin if kind=='pending' else launch)
                    view.cancel_button.configure(text='닫기',state='normal')
                    if closing[0]:window.destroy();return
        except queue.Empty:pass
        window.after(120,poll)
    window.protocol('WM_DELETE_WINDOW',close)
    if root is None:begin()
    poll();window.mainloop()
    if result_path:write_report(result_path,outcome)
    return outcome

def main(argv=None):
    parser=argparse.ArgumentParser()
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--install',action='store_true');mode.add_argument('--open',action='store_true');mode.add_argument('--supervise',action='store_true');mode.add_argument('--diagnose',action='store_true')
    parser.add_argument('--tag');parser.add_argument('--config');parser.add_argument('--result');parser.add_argument('--root');parser.add_argument('--launcher-source');parser.add_argument('--mapping-evidence')
    args, unknown=parser.parse_known_args(argv)
    if unknown and not args.open: parser.error('Unrecognized arguments')
    resources=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))
    config={};phase='startup'
    try:
        if args.open or args.supervise:
            root=canonical_root(args.root or Path(sys.executable).parent)
            open_active(root); return 0
        config_path=Path(args.config) if args.config else resources/'config.json'
        config=json.loads(config_path.read_text(encoding='utf-8-sig'))
        if not (args.install or args.diagnose):
            result=show_gui(config,None,explicit_root=args.root,tag=args.tag,launcher_source=args.launcher_source,resource_root=resources,result_path=args.result)
            return 1 if isinstance(result,dict) and result.get('status')=='FAILED' else 0
        phase='location'
        root=resolve_setup_root(config,explicit=args.root)
        if args.diagnose:
            from contentrium_cut.windows_install import DEFAULT_UPIA
            result=dict(schemaVersion=1,status='DIAGNOSED',appVersion=config.get('appVersion'),installationChanged=False,
                        checks=dict(installationRootResolved=True,adobeInstallerPresent=DEFAULT_UPIA.is_file(),premiereClosed=WindowsInstallation(root).host_exited()))
            if args.result:write_report(args.result,result)
            if sys.stdout:print(json.dumps(result))
            return 0
        evidence=None
        if args.mapping_evidence:
            source=Path(args.mapping_evidence);_guard_path(source)
            if source.stat().st_size>4096:raise CutError('BOOTSTRAP_MAPPING','Mapping receipt exceeds limits.')
            evidence=_json(source.read_bytes())
        result=install(config,root,args.tag,launcher_source=args.launcher_source,resource_root=resources,mapping_evidence=evidence)
        if args.result: _atomic_json(Path(args.result).resolve(),result)
        if sys.stdout: print(json.dumps(result))
        return 0
    except Exception as error:
        report=failure(error,phase,version=config.get('appVersion') or (args.tag[1:] if args.tag and args.tag.startswith('v') else None))
        if args.result: write_report(args.result,report)
        if args.install or args.diagnose:
            if sys.stderr: print(report['error']['code']+' '+report['error']['message'],file=sys.stderr)
        elif not (args.open or args.supervise):
            from contentrium_cut.installer_ui import InstallerWindow
            view=InstallerWindow('설치 환경 확인');view.failure(report);view.primary_button.configure(text='닫기',command=view.window.destroy);view.cancel_button.configure(command=view.window.destroy);view.window.mainloop()
        return 1

if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
