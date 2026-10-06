"""Explicit sync alternatives. Invalid clocks yield review, never an apply offset."""
import datetime
import math
import re
from fractions import Fraction
from .contract import CutError
from .models import check_cancel
from .analysis_inputs import channel_index

SR=16000
def _fail(code):raise CutError(code,'Confirm manual correspondence or a consistent embedded recording clock.')
def _decimal(value):
    if isinstance(value,bool) or not isinstance(value,(str,int,float)):_fail('MANUAL_SYNC_INVALID')
    try:number=float(Fraction(str(value)))
    except (ValueError,ZeroDivisionError,OverflowError):_fail('MANUAL_SYNC_INVALID')
    if not math.isfinite(number):_fail('MANUAL_SYNC_INVALID')
    return number

def timecode_seconds(value,fps,drop_frame):
    if not isinstance(fps,dict) or set(fps)!={'num','den'} or type(fps.get('num')) is not int or type(fps.get('den')) is not int or fps['num']<=0 or fps['den']<=0:_fail('TIMECODE_FPS_MISMATCH')
    rate=Fraction(fps['num'],fps['den']);nominal=round(float(rate))
    if nominal not in {24,25,30,48,50,60,100,120}:_fail('TIMECODE_FPS_MISMATCH')
    match=re.fullmatch(r'(\d{2}):(\d{2}):(\d{2})([:;])(\d{2})',value) if isinstance(value,str) else None
    if not match:_fail('TIMECODE_INVALID')
    h,m,s,separator,f=match.groups();h,m,s,f=map(int,(h,m,s,f))
    if h>=24 or m>=60 or s>=60 or f>=nominal:_fail('TIMECODE_INVALID')
    if type(drop_frame) is not bool or (separator==';')!=drop_frame:_fail('TIMECODE_DROPFRAME_MISMATCH')
    count=((h*60+m)*60+s)*nominal+f
    if drop_frame:
        if rate not in (Fraction(30000,1001),Fraction(60000,1001)):_fail('TIMECODE_DROPFRAME_MISMATCH')
        drop=2 if nominal==30 else 4;minutes=60*h+m
        if m%10 and s==0 and f<drop:_fail('TIMECODE_INVALID')
        count-=drop*(minutes-minutes//10)
    return float(Fraction(count,1)/rate)

def _clock(source,metadata):
    confirmation=source.get('timecodeConfirmation',{})
    if not isinstance(confirmation,dict) or confirmation.get('confirmed') is not True:_fail('TIMECODE_CONFIRMATION_REQUIRED')
    if confirmation.get('reset') is not False:_fail('TIMECODE_RESET')
    clock=confirmation.get('clockId')
    if not isinstance(clock,str) or not clock.strip():_fail('TIMECODE_CLOCK_REQUIRED')
    try:
        date=confirmation.get('date');day=datetime.date.fromisoformat(date)
        if day.isoformat()!=date:raise ValueError()
    except (ValueError,TypeError):_fail('TIMECODE_DATE_REQUIRED')
    values={item.get('tags',{}).get('timecode') for item in metadata.get('streams',[])+[metadata.get('format',{})] if item.get('tags',{}).get('timecode')}
    if len(values)!=1:_fail('TIMECODE_METADATA_CONFLICT' if values else 'TIMECODE_MISSING')
    value=next(iter(values));fps=confirmation.get('fps');seconds=timecode_seconds(value,fps,confirmation.get('dropFrame'))
    if 'value' in confirmation and confirmation['value']!=value:_fail('TIMECODE_METADATA_CONFLICT')
    videos=[s for s in metadata.get('streams',[]) if s.get('codec_type')=='video']
    rates=[]
    for video in videos:
        try:rate=Fraction(video.get('avg_frame_rate') or video.get('r_frame_rate'))
        except (TypeError,ValueError,ZeroDivisionError):_fail('TIMECODE_FPS_MISMATCH')
        if rate<=0:_fail('TIMECODE_FPS_MISMATCH')
        rates.append(rate)
    if not rates or any(rate!=Fraction(fps['num'],fps['den']) for rate in rates):_fail('TIMECODE_FPS_MISMATCH')
    origins=[]
    try:format_origin=float(metadata.get('format',{}).get('start_time',0) or 0)
    except (TypeError,ValueError):_fail('TIMECODE_METADATA_CONFLICT')
    if not math.isfinite(format_origin):_fail('TIMECODE_METADATA_CONFLICT')
    for video in videos:
        try:origin=float(video.get('start_time',0) or 0)
        except (TypeError,ValueError):_fail('TIMECODE_METADATA_CONFLICT')
        if not math.isfinite(origin):_fail('TIMECODE_METADATA_CONFLICT')
        origins.append(origin-format_origin)
    if len(set(origins))!=1:_fail('TIMECODE_METADATA_CONFLICT')
    nominal=round(float(rates[0]));day_frames=86400*nominal
    if confirmation['dropFrame']:day_frames-=(2 if nominal==30 else 4)*(1440-144)
    return {'clockId':clock,'date':date,'dayOrdinal':day.toordinal(),'fps':fps,'dropFrame':confirmation['dropFrame'],'embeddedTimecode':value,'counterSeconds':seconds,'counterDaySeconds':float(Fraction(day_frames,1)/rates[0]),'videoOriginSeconds':origins[0]}

def explicit_sync(sources,reference,fps,probe,cancel=None):
    method=sources[0]['syncMethod'];plan={'schemaVersion':1,'referenceAssetId':reference,'method':method,'offsets':{reference:0.},'sources':{},'edges':[],'reviews':[]};meta={}
    for source in sources:
        check_cancel(cancel);asset=source['assetId'];info=probe(source,cancel);metadata=info['metadata'];meta[asset]=metadata
        streams=[s for s in metadata.get('streams',[]) if s.get('codec_type')=='audio'];stream=channel_index(source.get('streamIndex',0));channel=channel_index(source.get('channelIndex',0))
        if (streams and (stream>=len(streams) or channel>=int(streams[stream].get('channels',0)))) or (not streams and (stream!=0 or channel!=0)):raise CutError('MISSING_AUDIO','Selected synchronization stream/channel does not exist.')
        duration=metadata.get('format',{}).get('duration') or (streams[stream].get('duration') if streams else None)
        try:duration=float(duration)
        except (ValueError,TypeError):raise CutError('MISSING_AUDIO','A bounded media duration is required.')
        if not math.isfinite(duration) or duration<=0:raise CutError('MISSING_AUDIO','Media duration is invalid.')
        plan['sources'][asset]={'status':'accepted' if asset==reference else 'review','path':[reference] if asset==reference else [],'validSourceRange':{'startSample':0,'endSample':max(1,round(duration*SR)),'sampleRate':SR},'size':info['size'],'mtimeNs':info['mtimeNs'],'streamIndex':stream,'channelIndex':channel,'hasAudio':bool(streams)}
    by_asset={s['assetId']:s for s in sources};reference_clock=None;reference_error=None
    if method=='timecode':
        try:reference_clock=_clock(by_asset[reference],meta[reference])
        except CutError as error:reference_error=error
    for source in sources:
        check_cancel(cancel);asset=source['assetId']
        if asset==reference:continue
        try:
            if method=='manual':
                c=source.get('manualConfirmation',{})
                if not isinstance(c,dict) or c.get('confirmed') is not True:_fail('MANUAL_SYNC_CONFIRMATION_REQUIRED')
                offset=_decimal(c.get('offsetSeconds'));points=c.get('correspondences',[])
                if not isinstance(points,list):_fail('MANUAL_SYNC_INVALID')
                for point in points:
                    if not isinstance(point,dict):_fail('MANUAL_SYNC_INVALID')
                    a,b=_decimal(point.get('sourceSeconds')),_decimal(point.get('referenceSeconds'))
                    if a<0 or b<0 or a>plan['sources'][asset]['validSourceRange']['endSample']/SR or b>plan['sources'][reference]['validSourceRange']['endSample']/SR:_fail('MANUAL_SYNC_INVALID')
                    if abs((b-a)-offset)>fps['den']/fps['num']:_fail('MANUAL_SYNC_DRIFT')
                evidence={'method':'manual','confirmed':True,'offsetSeconds':str(offset),'correspondences':points}
            else:
                if reference_error:raise reference_error
                clock=_clock(source,meta[asset])
                if clock['clockId']!=reference_clock['clockId']:_fail('TIMECODE_CLOCK_MISMATCH')
                if clock['fps']!=reference_clock['fps']:_fail('TIMECODE_FPS_MISMATCH')
                if clock['dropFrame']!=reference_clock['dropFrame']:_fail('TIMECODE_DROPFRAME_MISMATCH')
                offset=(clock['dayOrdinal']-reference_clock['dayOrdinal'])*clock['counterDaySeconds']+clock['counterSeconds']-reference_clock['counterSeconds']+reference_clock['videoOriginSeconds']-clock['videoOriginSeconds']
                if abs(offset)>12*3600:_fail('TIMECODE_DATE_AMBIGUOUS')
                evidence=dict(clock,referenceClock=reference_clock,method='timecode')
            plan['offsets'][asset]=offset;plan['sources'][asset].update(status='accepted',path=[reference,asset],confirmationEvidence=evidence)
            plan['edges'].append({'fromAssetId':reference,'toAssetId':asset,'status':'accepted','offsetSeconds':offset,'method':method})
        except CutError as error:plan['reviews'].append({'code':error.code,'assetId':asset,'fallback':'manual'})
    check_cancel(cancel);return plan
