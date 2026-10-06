"""Local admission estimates; they are budgets, not promised model peak usage."""
import ctypes
import math
import os
import shutil
from pathlib import Path
from .contract import CutError

SR=16000
DEFAULT_SETTINGS={'minFreeDiskBytes':64*1024*1024,'modelMemoryReserveBytes':512*1024*1024,'mixedChunkSeconds':600,'mixedOverlapSeconds':20,'device':'cpu'}
BYTE_SETTINGS={'cacheBudgetBytes','maxDecodedBytes','minFreeDiskBytes','maxMemoryBytes','modelMemoryReserveBytes'}

def validate_settings_shape(value):
    if not isinstance(value,dict) or set(value)-BYTE_SETTINGS-{'mixedChunkSeconds','mixedOverlapSeconds','device'}:
        raise CutError('INVALID_AUDIO_SETTINGS','Only supported local resource settings can be changed.')

def validate_settings(value):
    validate_settings_shape(value)
    result=dict(DEFAULT_SETTINGS,**value)
    for key in BYTE_SETTINGS:
        if key in result:positive_number(result[key],key,True)
    chunk=positive_number(result['mixedChunkSeconds'],'mixedChunkSeconds');overlap=positive_number(result['mixedOverlapSeconds'],'mixedOverlapSeconds')
    if chunk>600 or overlap*2>=chunk:raise CutError('INVALID_AUDIO_SETTINGS','Chunk size must be at most 600 seconds and exceed twice its overlap.')
    if not isinstance(result['device'],str) or result['device'] not in {'cpu','auto','cuda'}:raise CutError('INVALID_AUDIO_SETTINGS','Choose CPU, auto, or CUDA inference.')
    return result


def positive_number(value, field, integer=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0 or (integer and type(value) is not int):
        raise CutError('INVALID_AUDIO_SETTINGS',field+' must be a positive finite '+('integer.' if integer else 'number.'))
    return value


def available_memory():
    if os.name=='nt':
        class Memory(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(name,ctypes.c_uint64) for name in
                ('totalPhys','availPhys','totalPage','availPage','totalVirtual','availVirtual','availExtended')]
        info=Memory();info.length=ctypes.sizeof(info)
        api=ctypes.WinDLL('kernel32',use_last_error=True);api.GlobalMemoryStatusEx.argtypes=[ctypes.POINTER(Memory)]
        if api.GlobalMemoryStatusEx(ctypes.byref(info)):return info.availPhys
    elif hasattr(os,'sysconf'):
        try:return os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
        except (ValueError,OSError):pass
    raise CutError('AUDIO_RESOURCE_LIMIT','Available system memory could not be verified.')


def preflight_pcm(duration,settings,directory,channels=1,mixed=False):
    if duration is None:raise CutError('AUDIO_RESOURCE_LIMIT','Provide a bounded source range when media duration is unknown.')
    duration=positive_number(duration,'decoded duration');settings=settings or {}
    required=math.ceil(duration*SR)*4*channels
    existing=sum(p.stat().st_size for p in Path(directory).iterdir() if p.is_file() and p.name.startswith(('pcm-','channels-','mixed-')))
    free=shutil.disk_usage(directory).free;reserve=settings.get('minFreeDiskBytes',64*1024*1024)
    positive_number(reserve,'minFreeDiskBytes',True)
    limit=settings.get('maxDecodedBytes')
    if limit is not None:positive_number(limit,'maxDecodedBytes',True)
    if required+existing>(limit if limit is not None else max(0,free-reserve)) or free<required+reserve:
        raise CutError('AUDIO_RESOURCE_LIMIT','Decoded PCM exceeds the disk or user budget.',
                       {'requiredPcmBytes':required,'existingPcmBytes':existing,'freeDiskBytes':free,'reserveBytes':reserve,'maxDecodedBytes':limit})
    memory=None
    if mixed:
        chunk=min(duration,positive_number(settings.get('mixedChunkSeconds',600),'mixedChunkSeconds'))
        model=positive_number(settings.get('modelMemoryReserveBytes',512*1024*1024),'modelMemoryReserveBytes',True)
        # WAV int16, conversion/tensor float32 and bounded writer temporaries.
        memory=math.ceil(chunk*SR)*12+model
        available=available_memory();budget=settings.get('maxMemoryBytes',available)
        positive_number(budget,'maxMemoryBytes',True)
        if memory>min(available,budget):
            raise CutError('AUDIO_RESOURCE_LIMIT','Mixed analysis exceeds available memory or the user budget.',
                           {'estimatedMemoryBytes':memory,'availableMemoryBytes':available,'maxMemoryBytes':budget})
    bound=min(math.ceil(duration*SR)*4+4*SR,max(0,(free-reserve)//channels))
    if limit is not None:bound=min(bound,max(0,(limit-existing)//channels))
    return {'requiredPcmBytes':required,'freeDiskBytes':free,'estimatedMemoryBytes':memory,'maxOutputBytes':bound}


def cache_status(cache_root):
    root=Path(cache_root);root.mkdir(parents=True,exist_ok=True)
    used=sum(p.stat().st_size for p in root.iterdir() if p.is_file())
    free=shutil.disk_usage(root).free
    return {'cacheBytes':used,'freeDiskBytes':free,'availableMemoryBytes':available_memory(),
            'suggestedCacheBudgetBytes':max(0,(free+used)//10)}


def prune_completed_cache(cache_root,budget_bytes,guard,cancel=None):
    """Scan in owned work; request a one-use authority permit for each deletion."""
    import json,re
    from .cache import envelope
    from .models import check_cancel
    positive_number(budget_bytes,'cacheBudgetBytes',True)
    if not callable(guard):raise CutError('RESOURCE_GATE_REQUIRED','Stopped, quiescent maintenance authorization is required.')
    root=Path(cache_root).resolve(strict=True);removed=[]
    with guard() as authorized:
        if authorized is not True:raise CutError('RESOURCE_MAINTENANCE_BUSY','Stop and drain work before cache maintenance.')
    candidates=[];used=0
    for path in root.iterdir():
        check_cancel(cancel)
        if not path.is_file():continue
        used+=path.stat().st_size
        if path.is_symlink() or not re.fullmatch(r'[0-9a-f]{64}\.json',path.name):continue
        try:
            value=json.loads(path.read_text(encoding='utf-8'))
            if value!=envelope(value['kind'],path.stem,value['result']):continue
            candidates.append((path.stat().st_mtime_ns,path,path.stat().st_size))
        except (ValueError,KeyError,TypeError,OSError,CutError):continue
    for _,path,size in sorted(candidates):
        check_cancel(cancel)
        if used<=budget_bytes:break
        # The permit is entry-specific and single-use. It does not hold any
        # controller lock during filesystem I/O; stop owns the worker's drain.
        with guard(path.name) as authorized:
            if authorized is not True:raise CutError('RESOURCE_MAINTENANCE_BUSY','Maintenance authority expired.')
            check_cancel(cancel);path.unlink()
        used-=size;removed.append(path.name)
    check_cancel(cancel)
    return {'cacheBytes':used,'budgetBytes':budget_bytes,'removed':removed,'budgetMet':used<=budget_bytes}
