"""Download the provider's pinned binary to this user's PC; never redistribute it."""
import hashlib,json,os,shutil,zipfile
from pathlib import Path
from urllib.request import urlopen
URL='https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip'
SHA='60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba'
root=Path(os.environ['LOCALAPPDATA'])/'Contentrium CUT'/'app'/'ffmpeg'
root.mkdir(parents=True,exist_ok=True)
archive=root/'provider.zip.part'
try:
    digest=hashlib.sha256()
    with urlopen(URL,timeout=30) as response,archive.open('wb') as out:
        size=0
        while chunk:=response.read(1024*1024):
            size+=len(chunk)
            if size>200*1024*1024:raise RuntimeError('Provider package exceeds bound')
            digest.update(chunk);out.write(chunk)
    if digest.hexdigest()!=SHA:raise RuntimeError('Pinned provider hash does not match')
    with zipfile.ZipFile(archive) as package:
        for wanted in ['ffmpeg.exe','ffprobe.exe','LICENSE','README.txt']:
            names=[n for n in package.namelist() if Path(n).name==wanted]
            if len(names)!=1:raise RuntimeError('Provider package is incomplete')
            with package.open(names[0]) as src,(root/wanted).open('wb') as dst:shutil.copyfileobj(src,dst)
    (root/'source-record.json').write_text(json.dumps({'source':URL,'packageSha256':SHA,'provider':'Gyan.dev','version':'9.0.2','license':'GPLv3','sourcePage':'https://www.gyan.dev/ffmpeg/builds/','redistributedByContentrium':False},indent=2),encoding='utf-8')
    print('Pinned provider FFmpeg installed locally.')
finally:
    archive.unlink(missing_ok=True)
