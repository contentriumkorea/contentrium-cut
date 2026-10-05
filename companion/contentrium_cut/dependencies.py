"""Install a pinned provider artifact on the user's PC, without redistributing it."""
import os
from pathlib import Path
import shutil
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import zipfile
from .contract import CutError
from .updater import validate_zip
from .windows_install import _guard_path, _hash, _atomic_json

FFMPEG_URL = 'https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip'
FFMPEG_SHA256 = '60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba'
MAX_PROVIDER_BYTES = 200 * 1024 * 1024
FILES = ('ffmpeg.exe', 'ffprobe.exe', 'LICENSE', 'README.txt')

def validate_provider_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment
            or parsed.port not in (None, 443) or parsed.hostname not in
            {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}
            or (parsed.hostname == 'github.com' and parsed.path != urlsplit(FFMPEG_URL).path)):
        raise CutError('DEPENDENCY_URL', 'Untrusted FFmpeg provider URL.')
    return url

class _Redirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_provider_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

class ProviderTransport:
    def download(self, destination):
        deadline, size = time.monotonic() + 1800, 0
        opener = build_opener(_Redirect())
        request = Request(validate_provider_url(FFMPEG_URL), headers={'User-Agent': 'Contentrium-CUT-Setup'})
        with opener.open(request, timeout=20) as response, Path(destination).open('xb') as output:
            validate_provider_url(response.geturl())
            if response.status != 200: raise CutError('DEPENDENCY_HTTP', 'FFmpeg provider download failed.')
            while True:
                if time.monotonic() > deadline: raise CutError('DEPENDENCY_TIMEOUT', 'FFmpeg provider download timed out.')
                chunk = response.read(1024 * 1024)
                if not chunk: break
                size += len(chunk)
                if size > MAX_PROVIDER_BYTES: raise CutError('DEPENDENCY_SIZE', 'FFmpeg provider artifact is too large.')
                output.write(chunk)
            output.flush(); os.fsync(output.fileno())

def install_ffmpeg(root, *, transport=None):
    app = Path(root).resolve() / 'app'
    target = app / 'ffmpeg'
    _guard_path(target); app.mkdir(parents=True, exist_ok=True)
    stage = app / ('.ffmpeg-stage-' + uuid.uuid4().hex); stage.mkdir()
    archive = stage / 'provider.zip.part'
    try:
        (transport or ProviderTransport()).download(archive)
        if archive.stat().st_size > MAX_PROVIDER_BYTES or _hash(archive) != FFMPEG_SHA256:
            raise CutError('DEPENDENCY_HASH', 'Pinned FFmpeg provider artifact hash does not match.')
        validate_zip(archive, max_uncompressed=512 * 1024 * 1024)
        with zipfile.ZipFile(archive) as package:
            for wanted in FILES:
                names = [info for info in package.infolist() if not info.is_dir() and Path(info.filename).name == wanted]
                if len(names) != 1: raise CutError('DEPENDENCY_PACKAGE', 'FFmpeg provider package is incomplete.')
                with package.open(names[0]) as source, (stage / wanted).open('xb') as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                    output.flush(); os.fsync(output.fileno())
        record = {'source': FFMPEG_URL, 'packageSha256': FFMPEG_SHA256, 'provider': 'Gyan.dev', 'version': '9.0.2',
                  'license': 'GPLv3', 'sourcePage': 'https://www.gyan.dev/ffmpeg/builds/',
                  'redistributedByContentrium': False, 'files': {name:_hash(stage / name) for name in FILES}}
        archive.unlink(); _atomic_json(stage / 'source-record.json', record)
        if target.exists():
            for name in FILES: _guard_path(target / name)
            if not all((target / name).is_file() and _hash(target / name) == record['files'][name] for name in FILES):
                raise CutError('DEPENDENCY_EXISTS', 'Existing FFmpeg files differ; preserve them and choose a separate installation.')
            _atomic_json(target / 'source-record.json', record)
        else: os.replace(stage, target)
        return {'ffmpeg': str(target / 'ffmpeg.exe'), 'ffprobe': str(target / 'ffprobe.exe'), 'source': FFMPEG_URL}
    finally:
        if stage.exists():
            _guard_path(stage)
            if stage.resolve().parent != app:
                raise CutError('DEPENDENCY_PATH', 'Dependency staging path changed.')
            shutil.rmtree(stage)
