"""Durable at-most-one permits; host mutations and this file are not atomic.

Callers serialize through the service lock. Every update is copy/write/commit,
so a failed write cannot leak a permit or clear uncertainty in memory.
"""
import copy
import json
from pathlib import Path
import re
import time
import uuid
from .contract import CutError, canonical_hash
from .windows_install import _atomic_json as atomic_json

# 8,192 receipts cover 2,729 edit fragments (including outside-range pieces)
# and a failure-label receipt. Reserve the worst JSON size for every receipt:
# a 512-character result id may require twelve ASCII bytes per astral character.
MAX_BATCHES = 8192
MAX_STORAGE_BYTES = 64 * 1024 * 1024
MAX_BATCH_BYTES = len(json.dumps(dict(batchId=MAX_BATCHES,operationDigest='f'*64,resultSequenceRef='\U0001f600'*512,confirmed=False),ensure_ascii=True))+1
assert MAX_BATCHES*MAX_BATCH_BYTES < MAX_STORAGE_BYTES-1024*1024


def edit_batch_count(snapshot,plan,camera_tracks):
    from .contract import frame_ticks,tick_int
    first=tick_int(frame_ticks(snapshot['range']['startFrame'],snapshot['fps']))
    last=tick_int(frame_ticks(snapshot['range']['endFrame'],snapshot['fps']))
    fragments=len(plan['segments'])
    for clip in snapshot['clips']:
        if clip['mediaType']=='video' and clip['trackRef'] in camera_tracks and tick_int(clip['startTicks'])<last and tick_int(clip['endTicks'])>first:
            fragments+=int(tick_int(clip['startTicks'])<first)+int(tick_int(clip['endTicks'])>last)
    count=3*fragments+4
    if count+1>MAX_BATCHES:
        raise CutError('APPLY_CAPACITY_EXCEEDED',f'This edit requires {count} batches plus one recovery batch; the limit is {MAX_BATCHES}. Split the review range or reduce cut density.',{'requiredBatches':count,'maxBatches':MAX_BATCHES,'fragments':fragments})
    return count


def reject(code):
    raise CutError(code, 'Native apply evidence requires attention.')


def text_id(value):
    return isinstance(value, str) and 0 < len(value) <= 512 and not any(ord(c) < 32 for c in value)


def digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


class ApplyJournal:
    LIMIT = 512
    def __init__(self, root):
        self.path = Path(root) / 'reviews' / 'apply-journal.json'
        self.records = {}; self.corrupt = False
        if not self.path.exists(): return
        try:
            if self.path.stat().st_size > MAX_STORAGE_BYTES+1024: raise ValueError()
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if set(data) != {'schemaVersion','records','digest'} or data['schemaVersion'] != 1: raise ValueError()
            if data['digest'] != canonical_hash({'schemaVersion':1,'records':data['records']}): raise ValueError()
            records = data['records']
            if not isinstance(records,dict) or len(records) > self.LIMIT: raise ValueError()
            for key, record in records.items():
                required = {'applyId','requestId','requestHash','owner','instanceId','hostIdentity','epoch',
                            'appVersion','bundleId','protocolVersion','projectRef','sequenceRef','snapshotHash',
                            'planHash','kind','active','batchRunning','phase','batches','resultSequenceRef','createdAt'}
                optional = {'terminal','acknowledged','sourceRecordsDigest','capabilityId','lastFailure','batchLimit'}
                if (not isinstance(record,dict) or not required <= record.keys() or set(record)-required-optional or
                    key != record['applyId'] or not text_id(record['owner']) or not text_id(record['requestId']) or
                    not digest(record['planHash']) or not digest(record['requestHash']) or
                    record['kind'] not in {'edit','sync','input'} or type(record['epoch']) is not int or
                    type(record['active']) is not bool or type(record['batchRunning']) is not bool or
                    not isinstance(record['batches'],list) or len(record['batches']) > MAX_BATCHES or
                    type(record.get('batchLimit',MAX_BATCHES)) is not int or not 1<=record.get('batchLimit',MAX_BATCHES)<=MAX_BATCHES or
                    len(record['batches'])>record.get('batchLimit',MAX_BATCHES) or
                    record['phase'] not in {'AUTHORIZED','BATCH_INTENT','BATCH_CONFIRMED','UNCERTAIN','COMPLETED','FAILED','CANCELED','RECOVERED'}):
                    raise ValueError()
                for ordinal,batch in enumerate(record['batches'],1):
                    if (set(batch) != {'batchId','operationDigest','resultSequenceRef','confirmed'} or
                        batch['batchId'] != ordinal or not digest(batch['operationDigest']) or
                        type(batch['confirmed']) is not bool): raise ValueError()
                if record['active']: record['phase'] = 'UNCERTAIN'
            self._commit(records)
        except Exception:
            self.records = {}; self.corrupt = True

    def _commit(self, records):
        value = {'schemaVersion':1,'records':records}
        while len(json.dumps(value,ensure_ascii=True))>MAX_STORAGE_BYTES:
            terminal=sorted((r for r in records.values() if not r['active']),key=lambda r:r['createdAt'])
            if not terminal:reject('APPLY_JOURNAL_FULL')
            del records[terminal[0]['applyId']]
        atomic_json(self.path, dict(value,digest=canonical_hash(value)))
        # Keep the dict identity used by service participant accounting stable.
        self.records.clear(); self.records.update(records)

    def _set(self, value):
        records = copy.deepcopy(self.records); records[value['applyId']] = value
        self._commit(records)

    def blocked(self, except_id=None):
        return self.corrupt or any(r['active'] and r['applyId'] != except_id for r in self.records.values())

    def status(self, owner):
        fields = ['applyId','requestId','phase','epoch','planHash','batchRunning','resultSequenceRef','active']
        return {'blocked':self.blocked(),'records':[{key:r[key] for key in fields} for r in self.records.values() if r['owner']==owner],
                'error': {'code':'APPLY_JOURNAL_CORRUPT'} if self.corrupt else None}

    def get(self, owner, identity, epoch=None):
        if self.corrupt: reject('APPLY_JOURNAL_CORRUPT')
        record = self.records.get(identity)
        if not record or record['owner'] != owner: reject('APPLY_SCOPE')
        if epoch is not None and (type(epoch) is not int or epoch != record['epoch']): reject('APPLY_SCOPE')
        return copy.deepcopy(record)

    def replay(self, owner, request_id, request_hash):
        if not text_id(request_id) or len(request_id)>128: reject('INVALID_REQUEST')
        if self.corrupt: reject('APPLY_JOURNAL_CORRUPT')
        record = next((r for r in self.records.values() if r['owner']==owner and r['requestId']==request_id),None)
        if record:
            if record['requestHash'] != request_hash: reject('APPLY_REQUEST_CONFLICT')
            return dict(applyId=record['applyId'],epoch=record['epoch'],planHash=record['planHash'],
                        status=record['phase'],execute=False,replayed=True)

    def begin(self, binding, request_id, plan, request_hash=None):
        limit=binding.get('batchLimit',MAX_BATCHES)
        if type(limit) is not int or not 1<=limit<=MAX_BATCHES:
            raise CutError('APPLY_CAPACITY_EXCEEDED','Split the review range or reduce cut density before applying.')
        if len(json.dumps(binding,ensure_ascii=True))>1024*1024-4096:reject('APPLY_CAPACITY_EXCEEDED')
        request_hash = request_hash or canonical_hash(binding)
        prior = self.replay(binding['owner'],request_id,request_hash)
        if prior: return prior
        if self.blocked(): reject('APPLY_BUSY')
        records = copy.deepcopy(self.records)
        if len(records) >= self.LIMIT:
            terminal = sorted((r for r in records.values() if not r['active']),key=lambda r:r['createdAt'])
            if not terminal: reject('APPLY_JOURNAL_FULL')
            del records[terminal[0]['applyId']]
        identity = uuid.uuid4().hex
        value = dict(copy.deepcopy(binding),applyId=identity,requestId=request_id,requestHash=request_hash,
                     active=True,batchRunning=False,phase='AUTHORIZED',batches=[],resultSequenceRef=None,createdAt=time.time())
        records[identity]=value; self._commit(records)
        return dict(applyId=identity,epoch=value['epoch'],execute=True,planHash=value['planHash'],plan=plan)

    def batch(self, owner, identity, epoch, ordinal, operation_digest, result_ref):
        value = self.get(owner,identity,epoch)
        if type(ordinal) is not int or not 1<=ordinal<=value.get('batchLimit',MAX_BATCHES) or not digest(operation_digest): reject('INVALID_REQUEST')
        if result_ref is not None and not text_id(result_ref):reject('INVALID_REQUEST')
        proposed = dict(batchId=ordinal,operationDigest=operation_digest,resultSequenceRef=result_ref,confirmed=False)
        if ordinal <= len(value['batches']):
            prior = value['batches'][ordinal-1]
            if any(prior[k]!=proposed[k] for k in proposed if k!='confirmed'): reject('APPLY_BATCH_CONFLICT')
            return dict(applyId=identity,batchId=ordinal,execute=False,replayed=True)
        if not value['active'] or value['phase']=='UNCERTAIN': reject('APPLY_RECOVERY_REQUIRED')
        if value['batchRunning'] or ordinal != len(value['batches'])+1: reject('APPLY_BATCH_ORDER')
        if result_ref != value['resultSequenceRef']: reject('APPLY_RESULT_CONFLICT')
        value['batches'].append(proposed); value.update(batchRunning=True,phase='BATCH_INTENT'); self._set(value)
        return dict(applyId=identity,epoch=epoch,batchId=ordinal,execute=True,allowed=True)

    def batch_end(self, owner, identity, epoch, ordinal, receipt):
        value = self.get(owner,identity,epoch)
        if not isinstance(receipt,dict) or set(receipt)!={'transactionReturned'} or receipt['transactionReturned'] is not True or type(ordinal) is not int or not 1<=ordinal<=len(value['batches']): reject('INVALID_REQUEST')
        batch = value['batches'][ordinal-1]
        if not batch['confirmed']:
            if ordinal != len(value['batches']) or not value['batchRunning']: reject('APPLY_BATCH_ORDER')
            batch['confirmed']=True; value['batchRunning']=False
            if value['phase']!='UNCERTAIN': value['phase']='BATCH_CONFIRMED'
            self._set(value)
        return dict(applyId=identity,batchId=ordinal,confirmed=True)

    def result(self, owner, identity, epoch, result_ref):
        value = self.get(owner,identity,epoch)
        if not text_id(result_ref) or result_ref==value['sequenceRef']: reject('APPLY_RESULT_CONFLICT')
        if value['resultSequenceRef'] is not None:
            if result_ref!=value['resultSequenceRef']: reject('APPLY_RESULT_CONFLICT')
        else:
            if not value['active'] or not value['batches']: reject('APPLY_BATCH_REQUIRED')
            value['resultSequenceRef']=result_ref; self._set(value)
        return dict(applyId=identity,resultSequenceRef=result_ref)

    def end(self, owner, identity, epoch, status, receipt):
        value = self.get(owner,identity,epoch)
        if status not in {'completed','failed','canceled'} or not isinstance(receipt,dict): reject('INVALID_REQUEST')
        terminal = dict(status=status,receipt=receipt)
        if 'terminal' in value:
            if value['terminal']!=terminal: reject('APPLY_END_CONFLICT')
            return dict(applyId=identity,status=status,hostReported=True)
        if not value['active']: reject('APPLY_RECOVERY_REQUIRED')
        if status=='completed':
            common = {'planHash','resultSequenceRef','resultSnapshotHash','saved'}
            expected = common | ({'sourceRecordsDigest','priorSequencesHash','originalsUnchanged'} if value['kind']=='input' else {'sourceSnapshotHash','sourceUnchanged','readback'})
            if (set(receipt)!=expected or receipt['planHash']!=value['planHash'] or not digest(receipt['resultSnapshotHash']) or
                receipt['saved'] is not True or not value['resultSequenceRef'] or receipt['resultSequenceRef']!=value['resultSequenceRef'] or
                value['batchRunning'] or not value['batches'] or value['phase']=='UNCERTAIN'): reject('HOST_READBACK_REQUIRED')
            if value['kind']=='input':
                if receipt['sourceRecordsDigest']!=value['sourceRecordsDigest'] or not digest(receipt['priorSequencesHash']) or receipt['originalsUnchanged'] is not True: reject('HOST_READBACK_REQUIRED')
            elif (receipt['sourceSnapshotHash']!=value['snapshotHash'] or receipt['sourceUnchanged'] is not True or receipt['readback']!={'verified':True}): reject('HOST_READBACK_REQUIRED')
            value.update(active=False,phase='COMPLETED',terminal=terminal)
        else:
            if set(receipt)!={'resultSequenceRef'} or receipt['resultSequenceRef']!=value['resultSequenceRef']: reject('APPLY_RESULT_CONFLICT')
            if value['batches'] or value['resultSequenceRef']:
                value.update(phase='UNCERTAIN',lastFailure=terminal)
            else: value.update(active=False,phase=status.upper(),terminal=terminal)
        self._set(value)
        return dict(applyId=identity,status=status,hostReported=True)

    def recover(self, owner, request_id, identity, acknowledged, host_exited):
        if acknowledged is not True or (request_id is not None and not text_id(request_id)) or (identity is not None and not text_id(identity)): reject('INVALID_REQUEST')
        if self.corrupt: reject('APPLY_JOURNAL_CORRUPT')
        candidates=[r for r in self.records.values() if r['owner']==owner and
                    (identity is None or r['applyId']==identity) and (request_id is None or r['requestId']==request_id)]
        if request_id is None and identity is None: candidates=[r for r in candidates if r['active']]
        if not candidates:
            foreign=[r for r in self.records.values() if r['active']]
            if foreign and request_id is None and identity is None:
                # Same-install identity-loss recovery is explicit and requires
                # proof that EVERY original host exited; never cancel a live owner.
                if not all(host_exited(r['hostIdentity']) is True for r in foreign): reject('APPLY_HOST_EXIT_REQUIRED')
                records=copy.deepcopy(self.records)
                for record in foreign:records[record['applyId']].update(active=False,batchRunning=False,phase='RECOVERED',acknowledged=True)
                self._commit(records)
                return {'resolved':True,'recoveredCount':len(foreign),'resultSequenceRefs':[r['resultSequenceRef'] for r in foreign if r['resultSequenceRef']]}
            if foreign: reject('APPLY_RECOVERY_SCOPE')
            return {'resolved':True,'records':[]}
        if len(candidates)!=1: reject('APPLY_RECOVERY_SCOPE')
        value=copy.deepcopy(candidates[0])
        if value['active']:
            if (value['batches'] or value['resultSequenceRef']) and host_exited(value['hostIdentity']) is not True: reject('APPLY_HOST_EXIT_REQUIRED')
            value.update(active=False,batchRunning=False,phase='RECOVERED')
        value['acknowledged']=True; self._set(value)
        return dict(resolved=True,applyId=value['applyId'],resultSequenceRef=value['resultSequenceRef'])
