"""Verified, local model adapters. No inference-time download or ASR."""
import hashlib
import json
import math
import os
import re
import tempfile
import uuid
from pathlib import Path

import numpy as np

from .contract import CutError


def check_cancel(cancel):
    if cancel and cancel():
        raise CutError('CANCELED', 'Audio operation canceled.')


def _sha(path,cancel=None):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            check_cancel(cancel)
            h.update(block)
    check_cancel(cancel)
    return h.hexdigest()


class ModelManager:
    def __init__(self, root=None,cancel=None):
        self.root = Path(root or (Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Contentrium CUT' / 'models'))
        self.cancel=cancel

    def state(self, model,verify=True):
        # Metadata is display-only and reread on every status request. There is
        # no reusable integrity assertion: each real model load hashes again
        # in its owned worker, including same-path/same-stat replacements.
        if model not in ('silero', 'community-1'):
            raise CutError('INVALID_MODEL', 'Unsupported local model.')
        setup = {'provider': 'Hugging Face' if model == 'community-1' else 'Silero official GitHub',
                 'url': 'https://huggingface.co/pyannote/speaker-diarization-community-1' if model == 'community-1' else 'https://github.com/snakers4/silero-vad',
                 'requiresTermsAcceptance': model == 'community-1', 'audioUploadRequired': False,
                 'action': 'Accept provider terms in your account, then install a pinned local snapshot.' if model == 'community-1' else 'Install the official pinned Silero ONNX model and verified manifest.'}
        directory = (self.root / model).resolve()
        manifest_path = directory / 'manifest.json'
        result = {'modelId': model, 'status': 'not_installed', 'setup': setup}
        if not manifest_path.is_file():
            return result
        try:
            with manifest_path.open('rb') as stream:encoded=stream.read(1024*1024+1)
            if len(encoded)>1024*1024:raise ValueError('Model manifest exceeds the metadata limit.')
            manifest = json.loads(encoded)
            if not isinstance(manifest, dict):
                raise ValueError('Model manifest must be a JSON object.')
            if (manifest.get('modelId') != model or
                    not isinstance(manifest.get('revision'), str) or not manifest['revision'].strip() or
                    not isinstance(manifest.get('files'), dict) or not 1<=len(manifest['files'])<=1024 or
                    not isinstance(manifest.get('entrypoint'), str) or not manifest['entrypoint']):
                raise ValueError('Manifest requires modelId, revision and file hashes.')
            for name, digest in manifest['files'].items():
                if (not isinstance(name, str) or not name or Path(name).is_absolute() or
                        not isinstance(digest, str) or not re.fullmatch('[0-9a-fA-F]{64}', digest)):
                    raise ValueError('Model files require relative names and SHA256 hex strings.')
                if '..' in Path(name).parts:raise ValueError('Model path escapes its directory.')
                if not verify:continue
                check_cancel(self.cancel)
                path = (directory / name).resolve()
                if not path.is_relative_to(directory) or not path.is_file() or _sha(path,self.cancel) != digest.lower():
                    raise ValueError('Model file failed integrity check.')
            entry = directory / manifest['entrypoint']
            if not entry.is_relative_to(directory) or manifest['entrypoint'] not in manifest['files']:
                raise ValueError('Unverified model entrypoint.')
            if model == 'community-1' and len([name for name in manifest['files'] if name.endswith(('.bin','.safetensors'))]) < 2:
                raise ValueError('Community-1 requires local segmentation and embedding weights.')
            result.update(status='ready' if verify else 'installed', path=str(entry), revision=manifest['revision'], manifest=manifest)
        except (ValueError, KeyError, OSError, TypeError) as e:
            result.update(status='error', error=str(e))
        return result

    def resolve(self, model):
        status = self.state(model)
        if status['status'] != 'ready':
            raise CutError('MODEL_NOT_READY', 'Local model is not ready.', status)
        return status

    def install_community(self, token=None, terms_accepted=False, revision=None, cancel=None):
        """Explicit setup only. Token lives only in caller memory, never the manifest."""
        check_cancel(cancel)
        if not token or not terms_accepted or not revision or not re.fullmatch('[0-9a-fA-F]{40}',revision):
            raise CutError('MODEL_NOT_READY', 'Provider access and a pinned revision are required.',
                           {'setup': self.state('community-1')['setup']})
        try:
            from huggingface_hub import snapshot_download
            self.root.mkdir(parents=True,exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='community-download-',dir=self.root) as staging:
                directory=Path(staging)/'community-1'; directory.mkdir()
                snapshot_download(repo_id='pyannote/speaker-diarization-community-1',revision=revision,
                                  token=token,local_dir=str(directory))
                check_cancel(cancel)
                files={str(p.relative_to(directory)).replace('\\','/'):_sha(p) for p in directory.rglob('*')
                       if p.is_file() and '.cache' not in p.parts and p.name!='manifest.json'}
                if 'config.yaml' not in files or len([n for n in files if n.endswith(('.bin','.safetensors'))])<2:
                    raise CutError('MODEL_NOT_READY','Local snapshot is incomplete; install its offline weights.',
                                   {'setup':self.state('community-1')['setup']})
                manifest={'modelId':'community-1','revision':revision,'entrypoint':'config.yaml',
                          'source':'https://huggingface.co/pyannote/speaker-diarization-community-1','files':files}
                (directory/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
                check_cancel(cancel)
                destination=self.root/'community-1'; previous=None
                if destination.exists():
                    previous=self.root/('community-1.previous-'+uuid.uuid4().hex)
                    destination.replace(previous)
                try: directory.replace(destination)
                except Exception:
                    if previous is not None: previous.replace(destination)
                    raise
            return self.state('community-1')
        except CutError:
            raise
        except Exception:
            # Provider errors can contain authenticated URLs; do not include their text.
            raise CutError('MODEL_NOT_READY', 'Model download failed. Check account access and retry setup.',
                           {'setup': self.state('community-1')['setup']}) from None


class SileroVAD:
    def __init__(self, manager):
        status = manager.resolve('silero')
        self.revision = status['revision']
        try:
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.inter_op_num_threads = options.intra_op_num_threads = 1
            self.session = ort.InferenceSession(status['path'], sess_options=options, providers=['CPUExecutionProvider'])
        except Exception:
            raise CutError('MODEL_NOT_READY', 'Silero ONNX runtime could not load the verified model.', {'setup': status['setup']}) from None

    def probabilities(self, samples, cancel=None):
        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, 64), dtype=np.float32)
        for start in range(0, len(samples), 512):
            check_cancel(cancel)
            end = min(start + 512, len(samples))
            frame = np.zeros((1, 512), dtype=np.float32)
            frame[0, :end-start] = samples[start:end]
            data = np.concatenate((context, frame), axis=1)
            try:
                output, state = self.session.run(None, {'input': data, 'state': state, 'sr': np.array(16000, dtype=np.int64)})
            except Exception:
                raise CutError('MODEL_INFERENCE_FAILED', 'Silero inference failed.') from None
            context = data[:, -64:]
            yield start, end, float(np.asarray(output).ravel()[0])


class CommunityDiarizer:
    def __init__(self, manager,settings=None,cancel=None):
        from .resource import available_memory,positive_number
        check_cancel(cancel)
        status = manager.resolve('community-1')
        reserve=positive_number((settings or {}).get('modelMemoryReserveBytes',512*1024*1024),'modelMemoryReserveBytes',True)
        budget=positive_number((settings or {}).get('maxMemoryBytes',available_memory()),'maxMemoryBytes',True)
        if reserve>min(budget,available_memory()):raise CutError('AUDIO_RESOURCE_LIMIT','Local model load exceeds available memory or the user budget.')
        self.revision = status['revision']
        # Analysis is a separate worker process. These environment flags apply only there.
        os.environ['PYANNOTE_METRICS_ENABLED'] = '0'
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
        try:
            from pyannote.audio import Pipeline
            self.pipeline = Pipeline.from_pretrained(status['path'])
            if self.pipeline is None:
                raise ValueError('No local pipeline')
        except Exception:
            raise CutError('MODEL_NOT_READY', 'Community-1 local pipeline or offline dependencies are incomplete.',
                           {'setup': status['setup']}) from None
        self.configure(settings or {},cancel)

    def configure(self,settings,cancel=None):
        import torch
        from .resource import positive_number
        check_cancel(cancel);self.settings=dict(settings);self.reviews=[];self.device='cpu'
        maximum=positive_number(settings.get('mixedChunkSeconds',600),'mixedChunkSeconds')
        if maximum>600:raise CutError('INVALID_AUDIO_SETTINGS','Model contexts must be at most 600 seconds.')
        reserve=positive_number(settings.get('modelMemoryReserveBytes',512*1024*1024),'modelMemoryReserveBytes',True)
        requested=settings.get('device','cpu')
        if requested not in {'cpu','auto','cuda'}:raise CutError('INVALID_AUDIO_SETTINGS','Choose CPU, auto, or CUDA inference.')
        if requested!='cpu':
            available=False
            try:available=torch.cuda.is_available() and torch.cuda.mem_get_info()[0]>=reserve+math.ceil(maximum*16000)*6
            except (RuntimeError,AssertionError):pass
            if available:
                try:self.pipeline.to(torch.device('cuda'));self.device='cuda'
                except (RuntimeError,AssertionError):
                    self.pipeline.to(torch.device('cpu'));torch.cuda.empty_cache()
                    self.reviews.append({'code':'DEVICE_FALLBACK','from':'cuda','to':'cpu','reason':'DEVICE_TRANSFER_FAILED'})
            else:self.reviews.append({'code':'DEVICE_FALLBACK','from':requested,'to':'cpu','reason':'GPU_UNAVAILABLE_OR_BUDGET'})
        check_cancel(cancel)

    def turns(self, path, speaker_count=None, cancel=None):
        check_cancel(cancel)
        kwargs = {'num_speakers': int(speaker_count)} if speaker_count else {}
        try:
            def hook(*args, **hook_kwargs):
                check_cancel(cancel)
            # The analysis worker has already decoded the selected channel. Pass
            # PCM explicitly: the Windows CLI FFmpeg is not torchcodec's DLL ABI.
            import torch
            from scipy.io import wavfile
            from .resource import available_memory,positive_number
            rate, samples = wavfile.read(path,mmap=True)
            try:
                if rate != 16000 or samples.ndim != 1 or samples.dtype != np.int16:
                    raise CutError('INVALID_AUDIO_INPUT', 'Diarization requires decoded mono 16 kHz PCM.')
                settings=getattr(self,'settings',{});maximum=positive_number(settings.get('mixedChunkSeconds',600),'mixedChunkSeconds')
                memory=len(samples)*6+positive_number(settings.get('modelMemoryReserveBytes',512*1024*1024),'modelMemoryReserveBytes',True)
                budget=positive_number(settings.get('maxMemoryBytes',available_memory()),'maxMemoryBytes',True)
                if len(samples)>maximum*rate or memory>min(budget,available_memory()):raise CutError('AUDIO_RESOURCE_LIMIT','Chunk waveform exceeds available memory or the user budget.')
                check_cancel(cancel);pcm=samples.astype(np.float32);pcm/=32768.
            finally:
                if hasattr(samples,'_mmap'):samples._mmap.close()
            waveform = torch.from_numpy(pcm).unsqueeze(0)
            try:output = self.pipeline({'waveform': waveform, 'sample_rate': rate}, hook=hook, **kwargs)
            except RuntimeError as error:
                if getattr(self,'device','cpu')!='cuda' or not ('out of memory' in str(error).lower() or isinstance(error,torch.cuda.OutOfMemoryError)):raise
                check_cancel(cancel);self.pipeline.to(torch.device('cpu'));self.device='cpu';torch.cuda.empty_cache()
                self.reviews.append({'code':'DEVICE_FALLBACK','from':'cuda','to':'cpu','reason':'GPU_OUT_OF_MEMORY'})
                if memory>min(budget,available_memory()):raise CutError('AUDIO_RESOURCE_LIMIT','CPU retry exceeds available memory or the user budget.')
                output=self.pipeline({'waveform':waveform,'sample_rate':rate},hook=hook,**kwargs)
            # Never use exclusive_speaker_diarization: it discards true overlap.
            regular = output.speaker_diarization
            result = [(float(turn.start), float(turn.end), str(label))
                      for turn, _, label in regular.itertracks(yield_label=True)]
        except CutError:
            raise
        except MemoryError:
            raise CutError('AUDIO_RESOURCE_LIMIT','Local model memory was exhausted.') from None
        except Exception as error:
            if isinstance(error,RuntimeError) and 'out of memory' in str(error).lower():raise CutError('AUDIO_RESOURCE_LIMIT','Local model memory was exhausted.') from None
            raise CutError('MODEL_INFERENCE_FAILED', 'Community-1 local diarization failed.') from None
        check_cancel(cancel)
        return result
