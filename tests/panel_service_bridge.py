"""Private stdin test peer: real authenticated service, simulated native evidence.

No network listener, live installation, model provider or Premiere is used.
The seed command replaces only an already independently tested audio worker.
"""
import json
import hashlib
import os
import sys
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from contentrium_cut.contract import canonical_hash
from contentrium_cut.cache import validate_result
from contentrium_cut.service import CutService
from test_policy import fixture, speech
from test_private_auth import BOOTSTRAP, CONFIG
from test_s2_workers import ThreadContext, Scope
from contentrium_cut.jobs import atomic_json

# The private peer permits only temporary fixture files and injected native
# evidence; fail before any accidental dialog, provider process or socket.
for boundary in ['tkinter.Tk','tkinter.messagebox.showerror','subprocess.Popen','socket.socket']:
    patch(boundary,side_effect=AssertionError('Unexpected peer boundary: '+boundary)).start()
patch('contentrium_cut.apply_journal.atomic_json',atomic_json).start()
patch('contentrium_cut.resource.available_memory',return_value=8*1024**3).start()


# Node writes UTF-8 JSON to pipes; Windows' default cp949 corrupts signed Korean
# request bodies before they reach the real service, so pin this test transport.
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')


HOST = dict(pid=17, createTime=123, alive=True, processName='Adobe Premiere Pro.exe')
snapshot, analysis, mapping, policy = fixture()
snapshot.pop('snapshotHash')
snapshot['supportFlags']['hostApplyVerified'] = True
snapshot['snapshotHash'] = canonical_hash(snapshot)
analysis['intervals'] = [speech(0, 100, 'A'), speech(100, 200, 'B')]
analysis.update(modelRevision='fixture-completed-worker', reviews=[], evidence={},
                validAudioRanges=[dict(assetId='CA-0', startFrame=0, endFrame=200,
                                       startSample=0, endSample=320000, sampleRate=48000)])
validate_result('analysis', analysis)


with tempfile.TemporaryDirectory(prefix='cut-panel-service-') as directory:
    # Real bytes only in this private fixture; inference remains a seeded double.
    media_path = Path(directory) / 'owned-audio.wav'
    with wave.open(str(media_path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(b'\0\0' * 320000)
    snapshot['sources'][0]['canonicalPath'] = str(media_path)
    for asset in snapshot['sources'][1:]:
        camera=Path(directory)/(asset['assetId']+'.mov');camera.write_bytes(b'isolated camera media');asset['canonicalPath']=str(camera)
    snapshot['snapshotHash'] = canonical_hash({key: value for key, value in snapshot.items() if key != 'snapshotHash'})
    analysis['sourceInputs'] = [dict(assetId='CA-0', instanceKey='CA-0', path=str(media_path),
                                   sha256=hashlib.sha256(media_path.read_bytes()).hexdigest(),
                                   sessionOriginSeconds=0, sourceStartSeconds=0, sourceSampleRate=48000)]
    analysis.update(mediaSnapshotHash=snapshot['snapshotHash'],mediaInputs=[dict(assetId=s['assetId'],path=s['canonicalPath'],sha256=hashlib.sha256(Path(s['canonicalPath']).read_bytes()).hexdigest()) for s in snapshot['sources']])
    validate_result('analysis', analysis)

    def make_service():
        # Recovery observes only the simulated host. Never inspect a live OS PID.
        installation = SimpleNamespace(host_identity_exited=lambda identity: (
            HOST['alive'] is False and identity.get('pid') == HOST['pid']
            and identity.get('createTime') == HOST['createTime']))
        return CutService(directory, dict(CONFIG, privateBootstrap=BOOTSTRAP, sourceReader=lambda path: open(path, 'rb'),
                                          validationContext=ThreadContext(), validationScope=Scope), installation=installation,
                          process_probe=lambda instance, claim: dict(HOST) if HOST['alive'] else None)

    service = make_service()
    try:
        for line in sys.stdin:
            request = json.loads(line)
            command = request.get('command')
            if command == 'fixture':
                response = dict(bootstrap=BOOTSTRAP, bundle=CONFIG, snapshot=snapshot,
                                mapping=mapping, policy=policy, host=HOST)
            elif command == 'seed':
                if len(service.panels) != 1:
                    raise RuntimeError('Test fixture requires one authenticated panel.')
                owner = next(iter(service.panels))
                current = service.coordinator.session(owner)['snapshot']
                job = 'panel-service-analysis'
                service.jobs.jobs[job] = dict(jobId=job, kind='analysis', status='completed',
                                             epoch=0, result=analysis)
                service.jobs._save(service.jobs.jobs[job])
                service.record_job(job, owner, current['projectRef'], current['snapshotHash'])
                response = dict(jobId=job)
            elif command == 'restart':
                service.close()
                service = make_service()
                response = dict(restarted=True)
            elif command == 'host_exit':
                HOST['alive'] = False
                response = dict(simulatedHostExited=True)
            elif command == 'replace_media':
                before = media_path.stat()
                with media_path.open('r+b') as stream:
                    stream.seek(44)
                    stream.write(b'\x01\0' * 16)
                os.utime(media_path, ns=(before.st_atime_ns, before.st_mtime_ns))
                response = dict(replaced=True, snapshotHash=snapshot['snapshotHash'])
            elif command == 'http':
                status, body = service.dispatch_authenticated(request['method'], request['path'],
                                                              request['headers'], request['body'].encode())
                response = dict(status=status, body=body)
            else:
                raise RuntimeError('Unknown test peer command.')
            print(json.dumps(response, ensure_ascii=True), flush=True)
    finally:
        service.close()
