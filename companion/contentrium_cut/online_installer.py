"""Small anonymous downloader; full Setup retains all installation authority."""
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import uuid
from .contract import CutError
from .updater import UpdateManager, API_ROOT, BINARY_HEADERS, _json


def guard_destination(path):
    for item in (path, *path.parents):
        try: info=item.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise CutError('INSTALL_PATH','연결 폴더에는 내려받을 수 없습니다.')


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


class OnlineInstaller:
    def __init__(self,config,workspace,*,transport=None):
        self.manager=UpdateManager(workspace,config['publicKey'],'0.0.0',lambda epoch:None,None,
                                   transport=transport,signing_key_id=config['signingKeyId'])

    def _release(self,url):
        response=self.manager._get(url)
        if response.status!=200:raise CutError('UPDATE_HTTP','GitHub 배포 정보를 확인하지 못했습니다. 인터넷 연결을 확인하고 다시 시도해 주세요.')
        release=_json(response.body)
        if (release.get('name')!='Contentrium CUT' or release.get('draft') is not False or release.get('prerelease') is not False
                or type(release.get('id')) is not int or not isinstance(release.get('assets'),list)):
            raise CutError('UPDATE_RELEASE','공식 안정 버전을 확인하지 못했습니다.')
        assets=release['assets']
        if len({a['id'] for a in assets})!=len(assets) or len({a['name'].casefold() for a in assets})!=len(assets):
            raise CutError('UPDATE_ASSET','중복된 배포 파일을 사용할 수 없습니다.')
        return release

    def _documents(self,release):
        docs=[]
        for name,limit in [('update-manifest.json',1024*1024),('update-manifest.sig',16384)]:
            matches=[a for a in release['assets'] if a['name']==name]
            if len(matches)!=1 or matches[0].get('state')!='uploaded' or type(matches[0].get('size')) is not int or not 0<matches[0]['size']<=limit:
                raise CutError('UPDATE_ASSET','서명된 배포 정보가 불완전합니다.')
            response=self.manager.transport.get(matches[0]['browser_download_url'],dict(BINARY_HEADERS),timeout=15,max_bytes=limit)
            if response.status!=200 or len(response.body)!=matches[0]['size']:
                raise CutError('UPDATE_SIZE','서명된 배포 정보의 크기가 다릅니다.')
            docs.append(response.body)
        return docs

    def check(self):
        release=self._release(API_ROOT+'/releases/latest');documents=self._documents(release)
        manifest,reasons=self.manager._verify_manifest(*documents,release)
        # This downloader has no installation hooks. Full Setup independently
        # validates Adobe/host compatibility before any installation mutation.
        if set(reasons)-{'INSTALLER_NOT_CONFIGURED'}:
            raise CutError('INSTALL_COMPATIBILITY','이 PC와 호환되는 설치 버전이 아닙니다.',reasons)
        installers=[a for a in manifest['assets'] if a['role']=='installer']
        if len(installers)!=1 or not installers[0]['name'].lower().endswith('.exe') or installers[0]['size']>200*1024*1024:
            raise CutError('INSTALL_ASSET','검증된 설치 프로그램이 없습니다.')
        return dict(release=release,documents=documents,manifest=manifest,installer=installers[0])

    def confirm(self,selected):
        release=self._release(API_ROOT+'/releases/'+str(selected['release']['id']))
        def identity(value):
            return {k:value.get(k) for k in ('id','name','tag_name','draft','prerelease')} | {'assets':sorted(
                (a['id'],a['name'],a['size'],a.get('state'),a['browser_download_url']) for a in value['assets'])}
        if identity(release)!=identity(selected['release']) or self._documents(release)!=selected['documents']:
            raise CutError('UPDATE_RELEASE','선택한 배포 정보가 변경됐습니다. 설치 프로그램을 다시 실행해 주세요.')
        self.manager._verify_manifest(*selected['documents'],release)

    def download(self,selected,destination,cancel=lambda:False):
        path=Path(destination).absolute();guard_destination(path)
        if path.exists():raise CutError('INSTALL_DESTINATION','기존 파일은 덮어쓰지 않습니다. 다른 저장 경로를 선택해 주세요.')
        def check_cancel():
            if cancel():raise CutError('CANCELED','다운로드를 취소했습니다. 설치 파일을 실행하지 않았습니다.')
        check_cancel();self.confirm(selected)
        asset=selected['installer'];actual=next(a for a in selected['release']['assets'] if a['id']==asset['assetId'])
        partial=path.with_name(path.name+'.'+uuid.uuid4().hex+'.part')
        try:
            self.manager.transport.download(actual['browser_download_url'],partial,dict(BINARY_HEADERS),check_cancel,asset['size'])
            check_cancel();self._verify_file(selected,partial);self.confirm(selected);check_cancel()
            guard_destination(path)
            # Exclusive destination creation prevents clobbering a concurrent file.
            with path.open('xb') as output,partial.open('rb') as source:
                try:
                    for chunk in iter(lambda:source.read(1024*1024),b''):check_cancel();output.write(chunk)
                    output.flush();os.fsync(output.fileno());check_cancel()
                except BaseException:
                    output.close();path.unlink();raise
            self._verify_file(selected,path)
        finally:partial.unlink(missing_ok=True)
        return path

    def _verify_file(self,selected,path):
        guard_destination(path);asset=selected['installer']
        if path.stat().st_size!=asset['size'] or digest(path)!=asset['sha256']:
            raise CutError('UPDATE_HASH','다운로드 파일이 서명된 해시와 다릅니다. 실행하지 않았습니다.')

    def launch(self,selected,path,*,cancel=lambda:False):
        path=Path(path).absolute();self._verify_file(selected,path);self.confirm(selected)
        if cancel():raise CutError('CANCELED','설치 창을 열기 전에 취소했습니다.')
        self._verify_file(selected,path)
        return subprocess.run([str(path),'--tag',selected['manifest']['tag']],shell=False).returncode
