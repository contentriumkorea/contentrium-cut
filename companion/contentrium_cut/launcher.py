"""Hidden versioned runtime entry; no native GUI or manual pairing."""
import argparse
import json
from pathlib import Path
import sys
from .contract import CutError

def read_config(path=None):
    directory=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]
    candidate=Path(path) if path else directory/'config.json'
    if path and not candidate.is_file():raise CutError('LAUNCH_VERIFY','Explicit installed config is required.')
    if not candidate.is_file():candidate=Path(getattr(sys,'_MEIPASS',directory))/'config.json'
    value=json.loads(candidate.read_text(encoding='utf-8-sig'))
    return value,candidate

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--config');parser.add_argument('--root');parser.add_argument('--supervisor',action='store_true')
    parser.add_argument('--headless',action='store_true');parser.add_argument('--supervised',action='store_true')
    parser.add_argument('--recovery-only',action='store_true');parser.add_argument('--activation-stdin',action='store_true')
    parser.add_argument('--activation-update-id');parser.add_argument('--probe',action='store_true');parser.add_argument('--probe-output')
    args=parser.parse_args(argv)
    if args.probe:
        status=0
        try:
            config,config_path=read_config(args.config)
            from .audio import analyze_audio
            from .models import SileroVAD,CommunityDiarizer
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            import torch,onnxruntime
            from pyannote.audio import Pipeline
            value=json.dumps({'appVersion':config['appVersion'],'bundleId':config['bundleId'],'runtimeReady':True,'torchVersion':torch.__version__,'onnxVersion':onnxruntime.__version__})
        except Exception as error:
            status=1
            value=json.dumps({'runtimeReady':False,'errorCode':'RUNTIME_PROBE_FAILED','errorType':type(error).__name__})
        if args.probe_output:Path(args.probe_output).write_text(value,encoding='utf-8')
        elif sys.stdout:print(value)
        return status
    from .bootstrap import resolve_installation, canonical_root
    from .lifecycle import Supervisor, verify_runtime, run_headless, read_activation
    location=None
    try:
        location=resolve_installation(args.root)
        verified=verify_runtime(location,recovery=args.recovery_only)
        if (not getattr(sys,'frozen',False) or canonical_root(sys.executable)!=verified.executable or
                (args.config is not None and canonical_root(args.config)!=verified.config_path)):
            raise CutError('LAUNCH_VERIFY','Actual verified runtime/config is required.')
        if args.activation_update_id and not args.activation_stdin:
            raise CutError('UPDATE_HANDOFF','Protected activation input is required.')
        activation=read_activation(args.activation_update_id) if args.activation_stdin else None
        if args.supervisor:
            return Supervisor(location,activation=activation).run()
        return run_headless(location,verified,activation=activation,supervised=args.supervised)
    except Exception as error:
        if location:
            from .windows_install import _atomic_json
            _atomic_json(location.root/'runtime/lifecycle.json',dict(schemaVersion=1,installationId=location.installation_id,
                state='RECOVERY_REQUIRED',errorCode=getattr(error,'code','LIFECYCLE_FAILED')))
        return 1
