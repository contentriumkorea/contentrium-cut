"""Fail packaging closed if per-install material entered a public asset tree."""
import json
from pathlib import Path
from .contract import CutError
from .windows_install import _guard_path

PRIVATE_NAMES = {'contentrium-bootstrap.json','auth-bootstrap.json','contentrium-panel-identity.json',
                 'install-location.json','paired-sessions.json','service-job-owners.json','lifecycle.json',
                 'first-install.json','journal.json','settings.json','apply-journal.json','launcher-transaction.json','launcher-ownership.json',
                 'contentrium-install-challenge.json','contentrium-install-receipt.json','install-preparation.json','integration-registration.json',
                 'migration-registration.json'}
PRIVATE_FIELDS = {'secret','privateBootstrap','activationHandoff','clientProof','serverProof',
                  'sessionProof','requestKey','responseKey','token','tokenHash'}


def assert_public_tree(root):
    """Validate one public file or a runtime/plugin archive tree; return None.

    Validates paths, private filenames and exact public config documents.
    Does not inspect, log or return any private data values.
    """
    directory = Path(root); _guard_path(directory)
    if not directory.exists():raise CutError('RELEASE_INPUT','Public packaging input is missing.')
    def private(value):
        if isinstance(value,dict):return any(key in PRIVATE_FIELDS or private(item) for key,item in value.items())
        if isinstance(value,list):return any(private(item) for item in value)
        return False
    paths=[directory] if directory.is_file() else directory.rglob('*')
    for path in paths:
        _guard_path(path)
        relative=path.relative_to(directory).parts if directory.is_dir() else (path.name,)
        if path.is_file() and (path.name.casefold() in PRIVATE_NAMES or {'private','launcher-history','maintenance-history','migration-registration'} & {part.casefold() for part in relative}):
            raise CutError('RELEASE_PRIVATE_DATA','Private installation material cannot be packaged.')
        if path.is_file() and path.name in {'config.json','bundle.json','manifest.json','launcher-config.json'}:
            try:
                if path.stat().st_size>1024*1024 or private(json.loads(path.read_text(encoding='utf-8-sig'))):raise ValueError()
            except (ValueError,OSError,UnicodeError):
                raise CutError('RELEASE_PRIVATE_DATA','Public configuration cannot include private installation material.') from None
