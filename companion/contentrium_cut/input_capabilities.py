"""Bounded selected-media inspection. Capabilities expire with this service boot."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid
from .contract import CutError,canonical_hash
from .apply_journal import text_id
from .models import check_cancel


def fail(code='INPUT_SCOPE'):
    raise CutError(code,'Selected media must be inspected again.')


def identity(path,cancel=None):
    from .windows_install import _guard_path
    path=Path(path);_guard_path(path)
    if not path.is_absolute() or not path.is_file():fail('SOURCE_CHANGED')
    before=path.stat(); sha=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            check_cancel(cancel);sha.update(block)
    after=path.stat()
    stat=lambda s:(s.st_size,s.st_mtime_ns,s.st_ino,s.st_dev)
    if stat(before)!=stat(after):fail('SOURCE_CHANGED')
    return dict(size=after.st_size,mtimeNs=str(after.st_mtime_ns),inode=str(after.st_ino),device=str(after.st_dev),sha256=sha.hexdigest())


def trusted_probe(root):
    from .dependencies import FFMPEG_URL,FFMPEG_SHA256,FILES
    from .windows_install import _guard_path,_hash
    directory=Path(root)/'app'/'ffmpeg';_guard_path(directory)
    try:
        record=json.loads((directory/'source-record.json').read_text(encoding='utf-8'))
        if record['source']!=FFMPEG_URL or record['packageSha256']!=FFMPEG_SHA256 or set(record['files'])!=set(FILES):fail('FFMPEG_NOT_READY')
        for name in FILES:
            _guard_path(directory/name)
            if _hash(directory/name)!=record['files'][name]:fail('FFMPEG_NOT_READY')
        return str(directory/'ffprobe.exe')
    except (OSError,KeyError,ValueError):fail('FFMPEG_NOT_READY')


def run_probe(executable,path,cancel=None):
    check_cancel(cancel)
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        child=subprocess.Popen([executable,'-v','error','-show_streams','-show_format','-of','json',path],
                               stdin=subprocess.DEVNULL,stdout=output,stderr=errors,shell=False,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+20
        try:
            while child.poll() is None:
                check_cancel(cancel)
                if time.monotonic()>deadline or os.fstat(output.fileno()).st_size>2*1024*1024 or os.fstat(errors.fileno()).st_size>65536:fail('INPUT_PROBE_LIMIT')
                time.sleep(.02)
            check_cancel(cancel)
            if child.returncode or os.fstat(output.fileno()).st_size>2*1024*1024:fail('INPUT_PROBE_FAILED')
            output.seek(0)
            try:return json.load(output)
            except (ValueError,UnicodeError):fail('INPUT_PROBE_FAILED')
        finally:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=2)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=2)


def probe_sources(payload,cancel=None,probe=None):
    executable=trusted_probe(payload['root']) if probe is None else None
    assets=[]
    for source in payload['sources']:
        check_cancel(cancel);before=identity(source['path'],cancel)
        data=probe(source['path'],cancel) if probe else run_probe(executable,source['path'],cancel)
        streams=data.get('streams') if isinstance(data,dict) else None
        if not isinstance(streams,list) or len(streams)>256:fail('INPUT_PROBE_FAILED')
        safe=[]
        for stream in streams:
            if not isinstance(stream,dict) or type(stream.get('index')) is not int:fail('INPUT_PROBE_FAILED')
            kind=stream.get('codec_type')
            if kind not in {'video','audio'}:continue
            row=dict(index=stream['index'],type=kind)
            if kind=='audio':
                if type(stream.get('channels')) is not int or not 1<=stream['channels']<=256:fail('INPUT_PROBE_FAILED')
                row['channels']=stream['channels']
            safe.append(row)
        if identity(source['path'],cancel)!=before:fail('SOURCE_CHANGED')
        assets.append(dict(assetId=source['assetId'],identity=before,hasVideo=any(s['type']=='video' for s in safe),
                           hasAudio=any(s['type']=='audio' for s in safe),streams=safe))
    return dict(selectionId=payload['selectionId'],sourceRecordsDigest=payload['sourceRecordsDigest'],assets=assets)


class InputCapabilities:
    TTL=600
    def __init__(self,root,clock=time.monotonic):
        self.root=Path(root);self.clock=clock;self.selections={};self.capabilities={}

    def _put(self,table,value):
        now=self.clock()
        for key in list(table):
            if table[key]['expires']<now:del table[key]
        if len(table)>=128:fail('INPUT_CAPABILITY_LIMIT')
        key=uuid.uuid4().hex;table[key]=dict(value,expires=now+self.TTL);return key

    def select(self,owner,project,sources):
        if not text_id(project) or not isinstance(sources,list) or not 1<=len(sources)<=128:fail()
        seen=set()
        for source in sources:
            if not isinstance(source,dict) or set(source)!={'assetId','path','name','bounds'}:fail()
            if not text_id(source['assetId']) or source['assetId'] in seen or not isinstance(source['path'],str) or len(source['path'])>32767 or not Path(source['path']).is_absolute() or not isinstance(source['name'],str) or len(source['name'])>512:fail()
            seen.add(source['assetId']);bounds=source['bounds']
            if not isinstance(bounds,dict) or set(bounds)!={'VIDEO','AUDIO'}:fail()
            for value in bounds.values():
                if value is not None and (not isinstance(value,dict) or set(value)!={'in','out'} or
                    any(not isinstance(v,str) or not re.fullmatch('[0-9]{1,24}',v) for v in value.values()) or int(value['out'])<int(value['in'])):fail()
        record=dict(owner=owner,projectRef=project,sources=copy.deepcopy(sources),sourceRecordsDigest=canonical_hash(sources))
        key=self._put(self.selections,record)
        return dict(selectionId=key,sourceRecordsDigest=record['sourceRecordsDigest'])

    def _get(self,table,key,owner,project):
        record=table.get(key)
        if not record or record['owner']!=owner or record['projectRef']!=project or record['expires']<self.clock():fail()
        return record

    def payload(self,key,owner,project):
        record=self._get(self.selections,key,owner,project)
        return dict(root=str(self.root),selectionId=key,sources=copy.deepcopy(record['sources']),sourceRecordsDigest=record['sourceRecordsDigest'])

    def promote(self,key,owner,project,result,*,verified=False):
        record=self._get(self.selections,key,owner,project)
        if not isinstance(result,dict) or result.get('selectionId')!=key or result.get('sourceRecordsDigest')!=record['sourceRecordsDigest'] or len(result.get('assets',[]))!=len(record['sources']):fail()
        for source,asset in zip(record['sources'],result['assets']):
            if asset['assetId']!=source['assetId'] or any(type(asset.get(k)) is not bool for k in ['hasVideo','hasAudio']):fail()
            if not verified and identity(source['path'])!=asset['identity']:fail('SOURCE_CHANGED')
        cap=self._put(self.capabilities,dict(record,assets=copy.deepcopy(result['assets'])))
        return self.public(cap,self.capabilities[cap])

    def public(self,key,record):
        return dict(capabilityId=key,projectRef=record['projectRef'],sourceRecordsDigest=record['sourceRecordsDigest'],
                    assets=[{k:v for k,v in a.items() if k!='identity'} for a in record['assets']])

    def recheck(self,key,owner,project,*,verified=False):
        record=self._get(self.capabilities,key,owner,project)
        for source,asset in zip(record['sources'],record['assets']):
            if not verified and identity(source['path'])!=asset['identity']:fail('SOURCE_CHANGED')
        return record

    def plan(self,key,owner,project,choices,*,verified=False):
        record=self.recheck(key,owner,project,verified=verified)
        if not isinstance(choices,list) or len(choices)!=len(record['assets']):fail('INPUT_ROLES_REQUIRED')
        by_id={a['assetId']:a for a in record['assets']};seen=set()
        for choice in choices:
            if not isinstance(choice,dict) or set(choice)!={'assetId','role','outputAudio'}:fail('INPUT_ROLES_REQUIRED')
            asset=by_id.get(choice['assetId'])
            if not asset or choice['assetId'] in seen or choice['role'] not in {'camera','audio','exclude'} or type(choice['outputAudio']) is not bool:fail('INPUT_ROLES_REQUIRED')
            seen.add(choice['assetId'])
            if ((choice['role']=='camera' and not asset['hasVideo']) or (choice['role']=='audio' and not asset['hasAudio']) or
                (choice['outputAudio'] and (not asset['hasAudio'] or choice['role']=='exclude'))):fail('INPUT_STREAM_UNSUPPORTED')
        if not any(c['role']=='camera' for c in choices) or not any(c['outputAudio'] for c in choices):fail('INPUT_ROLES_REQUIRED')
        value=dict(kind='input',projectRef=project,sourceRecordsDigest=record['sourceRecordsDigest'],choices=copy.deepcopy(choices))
        return dict(value,planHash=canonical_hash(value)),self.public(key,record)
