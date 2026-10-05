"""Signed, fail-closed update orchestration independent of analysis workers.

The launcher owns the authenticated participant registry and concrete Adobe/
versioned-install hooks. No installer is inferred from a download or exit code.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from functools import total_ordering
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from .contract import CutError


API_ROOT = 'https://api.github.com/repos/contentriumkorea/contentrium-cut'
LATEST_URL = API_ROOT + '/releases/latest'
KEY_ID = 'contentrium-cut-2026-01'
API_HEADERS = {'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2026-03-10', 'User-Agent': 'Contentrium-CUT-Updater'}
BINARY_HEADERS = dict(API_HEADERS, Accept='application/octet-stream')
TERMINAL = {'IDLE', 'COMPLETE', 'CANCELED', 'FAILED_BEFORE_REPLACE', 'ROLLED_BACK'}
PRE_REPLACE = {'STOP_REQUESTED', 'QUIESCING', 'DOWNLOADING', 'VERIFYING_PACKAGE', 'WAITING_HOST_EXIT'}
CDN_HOSTS = {'objects.githubusercontent.com', 'release-assets.githubusercontent.com',
             'github-releases.githubusercontent.com'}


@total_ordering
class SemVer:
    """SemVer 2 precedence; build metadata never changes update ordering."""
    def __init__(self, value):
        match = re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
                             r'(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
                             r'(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?', value)
        if not match:
            raise ValueError('Invalid SemVer')
        self.core = tuple(int(match[i]) for i in (1, 2, 3))
        self.pre = tuple((match[4] or '').split('.')) if match[4] else ()
        if any(part.isdigit() and len(part) > 1 and part[0] == '0' for part in self.pre):
            raise ValueError('Invalid numeric prerelease identifier')

    def __eq__(self, other):
        if not isinstance(other, SemVer):
            return NotImplemented
        return (self.core, self.pre) == (other.core, other.pre)

    def __lt__(self, other):
        if not isinstance(other, SemVer):
            return NotImplemented
        if self.core != other.core:
            return self.core < other.core
        if not self.pre or not other.pre:
            return bool(self.pre) and not other.pre
        for left, right in zip(self.pre, other.pre):
            if left == right:
                continue
            if left.isdigit() and right.isdigit():
                return int(left) < int(right)
            if left.isdigit() != right.isdigit():
                return left.isdigit()
            return left < right
        return len(self.pre) < len(other.pre)


def validate_url(url):
    """Allow exact GitHub/CDN hosts only, including every redirect hop."""
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment
            or parsed.port not in (None, 443) or parsed.hostname not in CDN_HOSTS | {'github.com', 'api.github.com'}):
        raise CutError('UPDATE_URL', 'Untrusted update URL')
    if parsed.hostname == 'api.github.com' and not parsed.path.startswith('/repos/contentriumkorea/contentrium-cut/releases/'):
        raise CutError('UPDATE_URL', 'Unexpected GitHub API repository')
    if parsed.hostname == 'github.com' and not parsed.path.startswith('/contentriumkorea/contentrium-cut/releases/download/'):
        raise CutError('UPDATE_URL', 'Unexpected GitHub download repository')
    return url


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict
    body: bytes
    url: str


class GitHubTransport:
    """Anonymous, bounded metadata and streaming downloads without PATs."""
    def __init__(self):
        self.opener = build_opener(_SafeRedirect())

    def _open(self, url, headers, timeout):
        validate_url(url)
        try:
            result = self.opener.open(Request(url, headers=headers), timeout=timeout)
        except HTTPError as error:
            result = error
        validate_url(result.geturl())
        return result

    def get(self, url, headers, timeout=15, max_bytes=2 * 1024 * 1024):
        deadline = time.monotonic() + timeout
        with self._open(url, headers, min(timeout, 5)) as response:
            body = bytearray()
            while True:
                if time.monotonic() > deadline:
                    raise CutError('UPDATE_TIMEOUT', 'Metadata request timed out')
                chunk = response.read(min(65536, max_bytes + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise CutError('UPDATE_SIZE', 'Update metadata is too large')
            return HttpResponse(response.code, dict(response.headers.items()), bytes(body), response.geturl())

    def download(self, url, destination, headers, cancel, expected_size):
        deadline = time.monotonic() + 1800
        with self._open(url, headers, 15) as response:
            if response.code != 200:
                raise CutError('UPDATE_HTTP', 'Payload download failed', {'status': response.code})
            written = 0
            with open(destination, 'xb') as output:
                while True:
                    cancel()
                    if time.monotonic() > deadline:
                        raise CutError('UPDATE_TIMEOUT', 'Payload request timed out')
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > expected_size:
                        raise CutError('UPDATE_SIZE', 'Payload exceeds signed size')
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        cancel()
        if written != expected_size:
            raise CutError('UPDATE_SIZE', 'Payload does not match signed size')


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
    if not isinstance(value, dict):
        raise ValueError('Expected JSON object')
    return value


def _safe_name(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise CutError('UPDATE_PATH', 'Unsafe package path')
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ('', '.', '..') for part in name.rstrip('/').split('/')):
        raise CutError('UPDATE_PATH', 'Unsafe package path')
    for part in path.parts:
        if part.rstrip(' .') != part or re.search(r'[<>"|?*\x00-\x1f]', part):
            raise CutError('UPDATE_PATH', 'Unsafe Windows package path')
        if re.fullmatch(r'CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]', part.split('.')[0], re.I):
            raise CutError('UPDATE_PATH', 'Reserved Windows package path')
    return path


def validate_zip(path, max_uncompressed=4 * 1024**3):
    """Check every member before an installation hook may extract the archive."""
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 100000 or sum(member.file_size for member in members) > max_uncompressed:
            raise CutError('UPDATE_SIZE', 'Expanded package exceeds limits')
        seen = set()
        for member in members:
            normalized = str(_safe_name(member.filename)).casefold().rstrip('/')
            mode = member.external_attr >> 16
            if (normalized in seen or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))
                    or member.external_attr & 0x400 or member.flag_bits & 1):
                raise CutError('UPDATE_PATH', 'Duplicate, linked or encrypted package member')
            seen.add(normalized)
        bad_member = archive.testzip()
        if bad_member:
            raise CutError('UPDATE_PACKAGE', 'Corrupt package member')


class UpdateManager:
    """The caller serializes `advance` in an independent manager, never a worker.

    installation hooks: compatibility(manifest)->list[str], host_exited()->bool,
    snapshot(manifest)->dict, install(manifest, paths_by_role, snapshot)->dict,
    verify(version,bundle_id,activation_receipt)->bool, rollback(snapshot)->dict,
    verify_previous(snapshot)->bool. Snapshot must certify previousVerified=True.
    participants()->iterable[{id,identity}] and participant_exited(id,identity)
    must be sourced from the authenticated registry, not request body identities.
    """
    def __init__(self, root, public_key, current_version, stop_all, installation,
                 *, participants=None, participant_exited=None, transport=None,
                 signing_key_id=KEY_ID, updater_version='0.1.0', platform='windows',
                 architecture='x64', data_schema=1, clock=time.time, activation_handoff=None):
        self.root = Path(root).resolve()
        self.updates = self.root / 'updates'
        self.updates.mkdir(parents=True, exist_ok=True)
        self.journal = self.updates / 'journal.json'
        if isinstance(public_key, str):
            public_key = public_key.encode() if 'BEGIN PUBLIC KEY' in public_key else base64.b64decode(public_key, validate=True)
        self.key = load_pem_public_key(public_key) if public_key.startswith(b'-----BEGIN') else Ed25519PublicKey.from_public_bytes(public_key)
        if not isinstance(self.key, Ed25519PublicKey):
            raise ValueError('An Ed25519 public key is required')
        SemVer(current_version)
        self.current_version, self.updater_version = current_version, updater_version
        self.runtime_version = current_version  # Immutable version of this running executable's config.
        self.stop_all, self.installation = stop_all, installation
        self.participants = participants or (lambda: [])
        self.participant_exited = participant_exited or (lambda _id, _identity: False)
        self.transport, self.clock = transport or GitHubTransport(), clock
        self.signing_key_id = signing_key_id
        self.platform, self.architecture, self.data_schema = platform, architecture, data_schema
        self._lock, self._checking, self._operation = threading.RLock(), threading.Lock(), threading.Lock()
        self._canceled = threading.Event()
        self._stop_inflight = None
        self._journal_bad = False
        self._state = {'schemaVersion': 1, 'checkState': 'CHECK_FAILED', 'updateState': 'IDLE',
                       'updateId': None, 'updateEpoch': 0, 'epoch': 0, 'currentVersion': current_version,
                       'candidate': None, 'participants': {}, 'gateOpen': True, 'error': None,
                       'retryAt': 0, 'lastCheckedAt': None}
        if self.journal.exists():
            try:
                saved = _json(self.journal.read_bytes())
                self._validate_journal(saved)
                self._state.update(saved)
                self.current_version = saved.get('currentVersion', current_version)
                if saved.get('updateState') not in TERMINAL:
                    self._state.update(updateState='RECOVERY_REQUIRED', gateOpen=False,
                                       interruptedPhase=saved.get('updateState'),
                                       error={'code': 'UPDATE_INTERRUPTED', 'message': 'Interrupted update requires verified recovery'})
            except Exception:
                self._journal_bad = True
                self._state.update(updateState='RECOVERY_REQUIRED', gateOpen=False,
                                   error={'code': 'UPDATE_JOURNAL', 'message': 'Update journal could not be verified'})
        self._blocked = not self._state['gateOpen']
        if activation_handoff is not None:
            try:
                self.accept_activation_handoff(activation_handoff)
            except Exception as error:
                # Invalid authorization never changes the durable unused token or opens admission.
                self._state.update(updateState='RECOVERY_REQUIRED', gateOpen=False, error=self._error(error))
                self._blocked = True

    def _validate_journal(self, saved):
        """A phase label cannot substitute for durable evidence of that phase."""
        required = {'schemaVersion', 'checkState', 'updateState', 'updateId', 'updateEpoch', 'epoch',
                    'currentVersion', 'candidate', 'participants', 'gateOpen', 'error', 'retryAt', 'lastCheckedAt'}
        phases = TERMINAL | PRE_REPLACE | {'INSTALLING', 'PENDING_ACTIVATION', 'VERIFYING_INSTALL',
                                         'ROLLING_BACK', 'RECOVERY_REQUIRED'}
        if (not required <= saved.keys() or type(saved['schemaVersion']) is not int
                or saved['schemaVersion'] != 1 or type(saved['updateEpoch']) is not int
                or saved['updateEpoch'] < 0 or type(saved['epoch']) is not int
                or saved['epoch'] != saved['updateEpoch'] or saved['updateState'] not in phases
                or saved['checkState'] not in {'CHECKING', 'CURRENT', 'AVAILABLE', 'INCOMPATIBLE', 'CHECK_FAILED'}
                or type(saved['gateOpen']) is not bool or not isinstance(saved['participants'], dict)
                or type(saved['retryAt']) not in (int, float) or saved['retryAt'] < 0):
            raise ValueError('Incomplete or inconsistent update journal')
        SemVer(saved['currentVersion'])
        phase = saved['updateState']
        if (phase not in TERMINAL and saved['gateOpen']) or (phase in TERMINAL and not saved['gateOpen']):
            raise ValueError('Journal gate contradicts update phase')
        for participant_id, participant in saved['participants'].items():
            if (not isinstance(participant_id, str) or not isinstance(participant, dict)
                    or not {'identity', 'ackEpoch', 'receipt'} <= participant.keys()):
                raise ValueError('Incomplete participant evidence')
            epoch = participant['ackEpoch']
            if epoch is not None:
                receipt = participant['receipt']
                if (type(epoch) is not int or not 0 <= epoch <= saved['updateEpoch']
                        or not isinstance(receipt, dict) or receipt.get('quiescent') is not True
                        or receipt.get('batchRunning') is not False):
                    raise ValueError('Invalid participant acknowledgement')
        if phase == 'IDLE':
            if saved['updateId'] is not None or saved['updateEpoch'] != 0:
                raise ValueError('Idle journal contains an unfinished update')
            return
        update_fields = {'requestId', 'candidateId', 'manifestDigest', 'oldVersion', 'newVersion', 'bundleId',
                         'stopSignaled', 'stopInFlight', 'cancelRequested', 'preparedAssets', 'snapshot', '_attemptCandidate'}
        if (not update_fields <= saved.keys() or not isinstance(saved['updateId'], str)
                or not re.fullmatch('[0-9a-f]{32}', saved['updateId']) or saved['updateEpoch'] < 1
                or not isinstance(saved['requestId'], str) or not saved['requestId']
                or type(saved['stopSignaled']) is not bool or type(saved['stopInFlight']) is not bool
                or type(saved['cancelRequested']) is not bool or not isinstance(saved['preparedAssets'], dict)):
            raise ValueError('Missing update attempt identity/evidence')
        pinned = saved['_attemptCandidate']
        raw = base64.b64decode(pinned['raw'], validate=True)
        manifest, _ = self._verify_manifest(raw, base64.b64decode(pinned['signature'], validate=True), pinned['release'])
        digest = hashlib.sha256(raw).hexdigest()
        if (saved['manifestDigest'] != digest or saved['candidateId'] != f"{manifest['releaseId']}:{digest}"
                or saved['newVersion'] != manifest['appVersion'] or saved['bundleId'] != manifest['bundleId']
                or not SemVer(saved['oldVersion']) < SemVer(saved['newVersion'])):
            raise ValueError('Attempt does not match signed installation bundle')
        handoff = saved.get('_activationHandoff')
        if handoff is not None:
            if (not isinstance(handoff, dict)
                    or not re.fullmatch('[0-9a-f]{64}', handoff.get('tokenHash', ''))
                    or handoff.get('updateId') != saved['updateId']
                    or type(handoff.get('updateEpoch')) is not int or handoff['updateEpoch'] != saved['updateEpoch']
                    or handoff.get('newVersion') != saved['newVersion']
                    or handoff.get('issuerVersion') != saved['oldVersion']
                    or type(handoff.get('createdAt')) not in (int, float)
                    or type(handoff.get('expiresAt')) not in (int, float)
                    or not 0 < handoff['expiresAt'] - handoff['createdAt'] <= 300
                    or type(handoff.get('consumed')) is not bool
                    or (handoff['consumed'] and type(handoff.get('consumedAt')) not in (int, float))):
                raise ValueError('Invalid activation handoff evidence')
        installed = {'INSTALLING', 'PENDING_ACTIVATION', 'VERIFYING_INSTALL', 'COMPLETE', 'ROLLING_BACK', 'ROLLED_BACK'}
        if phase in installed:
            snapshot = saved['snapshot']
            if (saved['stopSignaled'] is not True or saved['stopInFlight']
                    or not isinstance(snapshot, dict) or snapshot.get('previousVerified') is not True
                    or snapshot.get('previousVersion') != saved['oldVersion'] or not snapshot.get('dataSnapshot')):
                raise ValueError('Replacement lacks verified previous-install/data snapshot')
            for asset in manifest['assets']:
                prepared = saved['preparedAssets'].get(asset['role'], {})
                expected = self.updates / saved['updateId'] / asset['name']
                if (prepared.get('assetId') != asset['assetId'] or prepared.get('sha256') != asset['sha256']
                        or prepared.get('path') != str(expected)):
                    raise ValueError('Missing signed prepared asset evidence')
        if phase in {'PENDING_ACTIVATION', 'VERIFYING_INSTALL', 'COMPLETE'} and not isinstance(saved.get('installerResult'), dict):
            raise ValueError('Pending activation lacks installer result evidence')
        if phase == 'COMPLETE':
            receipt = saved.get('activationReceipt', {})
            if (saved.get('activationVerified') is not True or saved['currentVersion'] != saved['newVersion']
                    or not isinstance(saved.get('installerResult'), dict)
                    or receipt.get('panelVersion') != saved['newVersion']
                    or receipt.get('companionVersion') != saved['newVersion']
                    or receipt.get('bundleId') != saved['bundleId'] or receipt.get('handshake') is not True
                    or receipt.get('dataReadable') is not True):
                raise ValueError('Completed update lacks matching activation evidence')
        elif phase in {'CANCELED', 'FAILED_BEFORE_REPLACE', 'ROLLED_BACK'}:
            evidence = saved.get('previousVerification', {})
            if (evidence.get('verified') is not True or evidence.get('version') != saved['oldVersion']
                    or type(evidence.get('checkedAt')) not in (int, float)
                    or saved['currentVersion'] != saved['oldVersion']
                    or (phase == 'ROLLED_BACK' and not isinstance(saved.get('rollbackResult'), dict))):
                raise ValueError('Terminal update lacks verified previous installation')

    @property
    def gate_open(self):
        with self._lock:
            return not self._blocked

    def assert_admitted(self, epoch=None):
        with self._lock:
            if self._blocked or (epoch is not None and epoch != self._state['updateEpoch']):
                raise CutError('UPDATE_IN_PROGRESS', 'CUT jobs are stopped for an update')
            return self._state['updateEpoch']

    @contextmanager
    def admission_guard(self, epoch=None):
        """Serialize a short admission/cache promotion against update start.

        Caller must acquire this guard BEFORE job locks; stage expensive work
        outside it. stop_all deliberately runs after the updater lock is released.
        """
        with self._lock:
            yield self.assert_admitted(epoch)

    def state(self):
        with self._lock:
            result = json.loads(json.dumps({key: value for key, value in self._state.items() if not key.startswith('_')}))
            result['gateOpen'] = not self._blocked
            return result

    def create_activation_handoff(self):
        """Issue one short-lived native-launch authorization; return the token once.

        The caller releases its process-wide installation mutex and becomes
        journal-read-only after launch. The recipient MUST own that same mutex
        before accepting. Tokens are never exposed by state() or written plaintext.
        """
        with self._operation, self._lock:
            saved = _json(self.journal.read_bytes())
            self._validate_journal(saved)
            if (self._journal_bad or self._state['updateState'] != 'PENDING_ACTIVATION'
                    or saved['updateState'] != 'PENDING_ACTIVATION'
                    or (saved['updateId'], saved['updateEpoch']) != (self._state['updateId'], self._state['updateEpoch'])
                    or self.runtime_version != saved['oldVersion'] or saved.get('_activationHandoff') is not None):
                raise CutError('UPDATE_HANDOFF', 'Activation handoff is unavailable')
            parent_identity = None
            if os.name == 'nt':
                from .windows_install import process_identity
                parent_identity = process_identity(os.getpid())
            token = secrets.token_urlsafe(32)
            now = self.clock()
            authorization = {'tokenHash': hashlib.sha256(token.encode('ascii')).hexdigest(),
                             'updateId': saved['updateId'], 'updateEpoch': saved['updateEpoch'],
                             'newVersion': saved['newVersion'], 'issuerVersion': self.runtime_version,
                             'parentIdentity': parent_identity, 'createdAt': now, 'expiresAt': now + 300,
                             'consumed': False, 'consumedAt': None}
            self._commit(_activationHandoff=authorization)
            return {'updateId': saved['updateId'], 'token': token, 'expiresAt': now + 300}

    def accept_activation_handoff(self, token):
        """Consume one bound token durably, keeping CUT admission closed.

        This continues only activation, never jobs. Normal reloads still require
        recovery. Actual file/Adobe evidence and a later host receipt are separate.
        """
        with self._operation, self._lock:
            if self._journal_bad or not isinstance(token, str) or not re.fullmatch('[A-Za-z0-9_-]{43}', token):
                raise CutError('UPDATE_HANDOFF', 'Activation handoff is invalid')
            saved = _json(self.journal.read_bytes())
            self._validate_journal(saved)
            authorization = saved.get('_activationHandoff') or {}
            now = self.clock()
            if (self._state['updateState'] != 'RECOVERY_REQUIRED'
                    or self._state.get('interruptedPhase') != 'PENDING_ACTIVATION'
                    or saved['updateState'] != 'PENDING_ACTIVATION'
                    or (saved['updateId'], saved['updateEpoch']) != (self._state['updateId'], self._state['updateEpoch'])
                    or self.runtime_version != saved['newVersion'] or authorization.get('consumed') is not False
                    or not authorization.get('createdAt', float('inf')) <= now < authorization.get('expiresAt', 0)
                    or not hmac.compare_digest(authorization.get('tokenHash', ''), hashlib.sha256(token.encode('ascii')).hexdigest())):
                raise CutError('UPDATE_HANDOFF', 'Activation handoff is invalid or expired')
            verify = getattr(self.installation, 'verify_application', None)
            if verify is None or verify(saved['newVersion'], saved['bundleId']) is not True:
                raise CutError('UPDATE_HANDOFF', 'Updated application could not be verified')
            updated = dict(saved, _activationHandoff=dict(authorization, consumed=True, consumedAt=now),
                           updateState='PENDING_ACTIVATION', gateOpen=False, error=None)
            updated.pop('interruptedPhase', None)
            self._validate_journal(updated)
            self._persist(updated)
            self._state = updated
            self._blocked = True
            return self.state()

    def _persist(self, value):
        temporary = self.journal.with_name('journal.' + uuid.uuid4().hex + '.tmp')
        try:
            with open(temporary, 'xb') as output:
                output.write(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8'))
                output.flush()
                os.fsync(output.fileno())
            if os.name == 'nt':
                import ctypes
                move = ctypes.WinDLL('kernel32', use_last_error=True).MoveFileExW
                move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
                if not move(str(temporary), str(self.journal), 0x1 | 0x8):
                    raise ctypes.WinError(ctypes.get_last_error())
            else:
                os.replace(temporary, self.journal)
                descriptor = os.open(self.updates, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def _commit(self, **changes):
        with self._lock:
            updated = dict(self._state, **changes)
            self._persist(updated)
            self._state = updated
            self._blocked = not updated['gateOpen']

    def _error(self, error):
        # Do not leak URLs with CDN query strings, credentials or local paths.
        return {'code': getattr(error, 'code', 'UPDATE_FAILED'),
                'message': getattr(error, 'message', 'Update operation failed')}

    def _get(self, url, headers=API_HEADERS, limit=2 * 1024 * 1024):
        validate_url(url)
        response = self.transport.get(url, dict(headers), 15, limit)
        validate_url(response.url)
        if len(response.body) > limit:
            raise CutError('UPDATE_SIZE', 'Update metadata is too large')
        return response

    def _rate_limit(self, response):
        if response.status not in (403, 429):
            return
        lowered = {key.lower(): value for key, value in response.headers.items()}
        retry = lowered.get('retry-after', '60')
        try:
            until = self.clock() + max(1, int(retry))
        except (ValueError, TypeError):
            try:
                until = max(self.clock() + 1, parsedate_to_datetime(retry).timestamp())
            except (ValueError, TypeError):
                until = self.clock() + 60
        if lowered.get('x-ratelimit-remaining') == '0':
            try:
                until = max(until, float(lowered.get('x-ratelimit-reset', until)))
            except ValueError:
                pass
        self._commit(retryAt=until)
        raise CutError('UPDATE_RATE_LIMIT', 'GitHub update request is rate limited')

    def _verify_manifest(self, raw, signature, release):
        wrapper, manifest = _json(signature), _json(raw)
        if (wrapper.get('algorithm') != 'Ed25519' or wrapper.get('keyId') != self.signing_key_id
                or manifest.get('signingKeyId') != self.signing_key_id):
            raise CutError('UPDATE_SIGNATURE', 'Unknown manifest signing key')
        try:
            self.key.verify(base64.b64decode(wrapper['signature'], validate=True), raw)
        except Exception as error:
            raise CutError('UPDATE_SIGNATURE', 'Manifest signature is invalid') from error
        required = {'schemaVersion', 'productId', 'displayName', 'appVersion', 'channel', 'releaseId',
                    'tag', 'builtAt', 'platform', 'architecture', 'panelVersion', 'companionVersion',
                    'bundleId', 'updateProtocolRange', 'minUpdaterVersion', 'dataSchemaFrom', 'dataSchemaTo',
                    'migrationId', 'assets', 'modelCompatibility', 'releaseNotes', 'signingKeyId'}
        if not required <= manifest.keys():
            raise CutError('UPDATE_MANIFEST', 'Manifest fields are missing')
        version = SemVer(manifest['appVersion'])
        if (type(manifest['schemaVersion']) is not int or manifest['schemaVersion'] != 1 or manifest['productId'] != 'com.contentrium.cut'
                or manifest['displayName'] != 'Contentrium CUT' or manifest['channel'] != 'stable'
                or manifest['releaseId'] != release['id'] or manifest['tag'] != release['tag_name']
                or manifest['tag'] != 'v' + manifest['appVersion'] or version.pre
                or manifest['panelVersion'] != manifest['appVersion']
                or manifest['companionVersion'] != manifest['appVersion']
                or not isinstance(manifest['bundleId'], str) or not manifest['bundleId']):
            raise CutError('UPDATE_MANIFEST', 'Product, release or version mismatch')
        built_at = datetime.fromisoformat(manifest['builtAt'].replace('Z', '+00:00'))
        if (built_at.tzinfo is None or type(manifest['releaseId']) is not int
                or not isinstance(manifest['platform'], str) or not isinstance(manifest['architecture'], str)
                or type(manifest['dataSchemaTo']) is not int or manifest['dataSchemaTo'] < 1
                or not isinstance(manifest['dataSchemaFrom'], list)
                or any(type(schema) is not int or schema < 1 for schema in manifest['dataSchemaFrom'])
                or not isinstance(manifest['migrationId'], str)
                or not re.fullmatch(r'[A-Za-z0-9._-]{1,120}', manifest['migrationId'])
                or not isinstance(manifest['modelCompatibility'], dict)
                or not isinstance(manifest['releaseNotes'], str)):
            raise CutError('UPDATE_MANIFEST', 'Invalid manifest field types')
        if not isinstance(manifest['assets'], list) or not manifest['assets']:
            raise CutError('UPDATE_MANIFEST', 'No installation assets')
        ids, names, roles = set(), set(), set()
        for asset in manifest['assets']:
            name = asset['name']
            if (len(_safe_name(name).parts) != 1 or name in {'update-manifest.json', 'update-manifest.sig'}
                    or type(asset['assetId']) is not int or asset['assetId'] <= 0
                    or type(asset['size']) is not int or asset['size'] <= 0
                    or not re.fullmatch('[0-9a-f]{64}', asset['sha256'])
                    or asset['role'] not in {'panel', 'companion', 'installer'}
                    or asset['assetId'] in ids or name.casefold() in names or asset['role'] in roles):
                raise CutError('UPDATE_MANIFEST', 'Invalid signed asset')
            actual = next((item for item in release['assets'] if item['id'] == asset['assetId']), None)
            if not actual or actual['name'] != name or actual['size'] != asset['size'] or actual.get('state') != 'uploaded':
                raise CutError('UPDATE_ASSET', 'Signed asset does not match GitHub metadata')
            validate_url(actual['browser_download_url'])
            ids.add(asset['assetId']); names.add(name.casefold()); roles.add(asset['role'])
        if not {'panel', 'companion'} <= roles:
            raise CutError('UPDATE_MANIFEST', 'Matched panel and Companion assets are required')
        reasons = []
        protocol = manifest['updateProtocolRange']
        if not isinstance(protocol, dict) or type(protocol.get('min')) is not int or type(protocol.get('max')) is not int:
            raise CutError('UPDATE_MANIFEST', 'Invalid updater protocol range')
        if not protocol['min'] <= 1 <= protocol['max'] or SemVer(self.updater_version) < SemVer(manifest['minUpdaterVersion']):
            reasons.append('UPDATER_PROTOCOL')
        if manifest['platform'].lower() != self.platform or manifest['architecture'].lower() != self.architecture:
            reasons.append('PLATFORM')
        if not isinstance(manifest['dataSchemaFrom'], list) or self.data_schema not in manifest['dataSchemaFrom']:
            reasons.append('DATA_SCHEMA')
        if self.installation is None:
            reasons.append('INSTALLER_NOT_CONFIGURED')
        else:
            reasons.extend(self.installation.compatibility(manifest))
        return manifest, reasons

    def check(self):
        with self._checking:
            with self._lock:
                if self._blocked:
                    return self.state()
                if self._state['retryAt'] > self.clock():
                    return self.state()
                headers = dict(API_HEADERS)
                cached = self._state.get('_verifiedRelease')
                if cached and self._state.get('_etag'):
                    headers['If-None-Match'] = self._state['_etag']
            try:
                self._commit(checkState='CHECKING', error=None)
                response = self._get(LATEST_URL, headers)
                self._rate_limit(response)
                if response.status == 304:
                    if not cached:
                        response = self._get(LATEST_URL)
                    else:
                        pinned = self._state['_candidate']
                        manifest, reasons = self._verify_manifest(base64.b64decode(pinned['raw']),
                                                                 base64.b64decode(pinned['signature']), pinned['release'])
                        newer = SemVer(self.current_version) < SemVer(manifest['appVersion'])
                        candidate = dict(cached['candidate']) if newer and cached['candidate'] else None
                        if candidate:
                            candidate.update(expiresAt=self.clock() + 3600, compatibilityReasons=reasons)
                        check_state = ('INCOMPATIBLE' if reasons else 'AVAILABLE') if newer else 'CURRENT'
                        self._commit(checkState=check_state, candidate=candidate,
                                     _verifiedRelease={'checkState': check_state, 'candidate': candidate},
                                     lastCheckedAt=self.clock(), error=None)
                        return self.state()
                if response.status != 200:
                    raise CutError('UPDATE_HTTP', 'GitHub update check failed', {'status': response.status})
                release = _json(response.body)
                if release.get('draft') is not False or release.get('prerelease') is not False:
                    raise CutError('UPDATE_RELEASE', 'Release is not a published stable release')
                if release.get('name') != 'Contentrium CUT' or type(release.get('id')) is not int:
                    raise CutError('UPDATE_RELEASE', 'Unexpected release identity')
                assets = release['assets']
                if len({item['id'] for item in assets}) != len(assets) or len({item['name'] for item in assets}) != len(assets):
                    raise CutError('UPDATE_ASSET', 'Duplicate GitHub assets')
                docs = []
                for name, limit in [('update-manifest.json', 1024 * 1024), ('update-manifest.sig', 16384)]:
                    asset = next(item for item in assets if item['name'] == name)
                    downloaded = self._get(asset['browser_download_url'], BINARY_HEADERS, limit)
                    if downloaded.status != 200 or len(downloaded.body) != asset['size']:
                        raise CutError('UPDATE_ASSET', 'Manifest asset download failed')
                    docs.append(downloaded.body)
                raw, signature = docs
                manifest, reasons = self._verify_manifest(raw, signature, release)
                digest = hashlib.sha256(raw).hexdigest()
                newer = SemVer(self.current_version) < SemVer(manifest['appVersion'])
                candidate = None
                check_state = 'CURRENT'
                if newer:
                    check_state = 'INCOMPATIBLE' if reasons else 'AVAILABLE'
                    candidate = {'candidateId': f"{release['id']}:{digest}", 'manifestDigest': digest,
                                 'releaseId': release['id'], 'appVersion': manifest['appVersion'],
                                 'bundleId': manifest['bundleId'], 'releaseNotes': manifest['releaseNotes'],
                                 'publishedAt': release.get('published_at'), 'size': sum(item['size'] for item in manifest['assets']),
                                 'compatibilityReasons': reasons, 'expiresAt': self.clock() + 3600}
                cached = {'checkState': check_state, 'candidate': candidate}
                with self._lock:
                    if self._blocked:
                        return self.state()
                    self._commit(checkState=check_state, candidate=candidate, error=None, lastCheckedAt=self.clock(),
                                 _verifiedRelease=cached, _etag=response.headers.get('ETag') or response.headers.get('etag'),
                                 _candidate={'raw': base64.b64encode(raw).decode(), 'signature': base64.b64encode(signature).decode(),
                                             'release': release, 'manifest': manifest})
            except Exception as error:
                with self._lock:
                    if not self._blocked:
                        self._commit(checkState='CHECK_FAILED', candidate=None, error=self._error(error))
            return self.state()

    def register_participant(self, participant_id, identity):
        with self._lock:
            participants = dict(self._state['participants'])
            previous = participants.get(participant_id)
            participants[participant_id] = {'identity': identity, 'ackEpoch': None, 'receipt': None}
            # Reconnecting does not inherit an old process's acknowledgement.
            if previous and previous['identity'] == identity:
                participants[participant_id] = previous
            self._commit(participants=participants)
            return self.state()

    def ack(self, participant_id, epoch, receipt):
        with self._lock:
            if epoch != self._state['updateEpoch'] or not self._blocked or participant_id not in self._state['participants']:
                raise CutError('UPDATE_EPOCH', 'Unknown participant or stale update epoch')
            if receipt.get('quiescent') is not True or receipt.get('batchRunning') is not False:
                raise CutError('UPDATE_QUIESCE', 'Participant has not confirmed quiescence')
            participants = json.loads(json.dumps(self._state['participants']))
            participants[participant_id].update(ackEpoch=epoch, receipt=receipt)
            self._commit(participants=participants)
            return self.state()

    def start(self, candidate_id, manifest_digest, request_id):
        epoch = None
        attempt = None
        try:
            with self._lock:
                if self._blocked:
                    if self._state.get('candidateId') == candidate_id and self._state.get('manifestDigest') == manifest_digest:
                        return self.state()
                    raise CutError('UPDATE_IN_PROGRESS', 'Another update is active')
                candidate = self._state['candidate']
                if (not isinstance(request_id, str) or not request_id or len(request_id) > 256
                        or not candidate or candidate['candidateId'] != candidate_id
                        or candidate['manifestDigest'] != manifest_digest or candidate['expiresAt'] <= self.clock()
                        or self._state['checkState'] != 'AVAILABLE'):
                    raise CutError('UPDATE_CANDIDATE', 'Update candidate is unknown, expired or incompatible')
                pinned = self._state['_candidate']
                raw = base64.b64decode(pinned['raw'])
                manifest, reasons = self._verify_manifest(raw, base64.b64decode(pinned['signature']), pinned['release'])
                actual_digest = hashlib.sha256(raw).hexdigest()
                if (actual_digest != manifest_digest or candidate_id != f"{manifest['releaseId']}:{actual_digest}"
                        or manifest['appVersion'] != candidate['appVersion'] or manifest['bundleId'] != candidate['bundleId']
                        or reasons or not SemVer(self.current_version) < SemVer(manifest['appVersion'])):
                    raise CutError('UPDATE_CANDIDATE', 'Selected signed bundle is not a compatible newer version')
                participants = dict(self._state['participants'])
                for item in self.participants():
                    participants[item['id']] = {'identity': item.get('identity', item), 'ackEpoch': None, 'receipt': None}
                epoch = self._state['updateEpoch'] + 1
                attempt = (uuid.uuid4().hex, epoch)
                self._stop_inflight = attempt
                self._blocked = True  # Even a failed journal write must close admission.
                self._canceled.clear()
                self._commit(updateState='STOP_REQUESTED', gateOpen=False, updateEpoch=epoch, epoch=epoch,
                             updateId=attempt[0], requestId=request_id, candidateId=candidate_id,
                             manifestDigest=manifest_digest, oldVersion=self.current_version,
                             newVersion=candidate['appVersion'], bundleId=candidate['bundleId'],
                             participants=participants, preparedAssets={}, snapshot=None, error=None,
                             stopSignaled=False, stopInFlight=True, cancelRequested=False,
                             _attemptCandidate=pinned, _activationHandoff=None)
        finally:
            # Workers may be checking admission while stop_all takes their locks.
            # Invoke outside the updater lock to avoid a gate/control deadlock.
            if epoch is not None:
                try:
                    self.stop_all(epoch)
                except Exception as error:
                    with self._lock:
                        if self._matches_attempt(attempt):
                            self._blocked = True
                            self._commit(updateState='RECOVERY_REQUIRED', gateOpen=False,
                                         stopInFlight=False, error=self._error(error))
                    raise
                finally:
                    with self._lock:
                        if self._stop_inflight == attempt:
                            self._stop_inflight = None
        with self._lock:
            if not self._matches_attempt(attempt):
                return self.state()
            self._commit(stopSignaled=True, stopInFlight=False)
        if self._canceled.is_set() and self._operation.acquire(blocking=False):
            try:
                with self._lock:
                    if self._matches_attempt(attempt):
                        self._finish_before_replace(CutError('CANCELED', 'Update canceled'))
            finally:
                self._operation.release()
        return self.state()

    def _matches_attempt(self, attempt):
        return attempt is not None and (self._state.get('updateId'), self._state['updateEpoch']) == attempt

    def _cancel_check(self):
        if self._canceled.is_set():
            raise CutError('CANCELED', 'Update download canceled')

    def _quiesced(self):
        with self._lock:
            for item in self.participants():
                identity = item.get('identity', item)
                existing = self._state['participants'].get(item['id'])
                if not existing or existing['identity'] != identity:
                    self.register_participant(item['id'], identity)
            for participant_id, item in self._state['participants'].items():
                if item['ackEpoch'] != self._state['updateEpoch'] and not self.participant_exited(participant_id, item['identity']):
                    return False
            return True

    def _candidate(self):
        pinned = self._state['_attemptCandidate']
        manifest, reasons = self._verify_manifest(base64.b64decode(pinned['raw']), base64.b64decode(pinned['signature']), pinned['release'])
        if reasons:
            raise CutError('UPDATE_COMPATIBILITY', 'Selected update is no longer compatible')
        return manifest, pinned['release']

    def _confirm_pinned_release(self, manifest, pinned_release):
        """Recheck the selected release ID, never substitute a changed latest."""
        response = self._get(API_ROOT + '/releases/' + str(manifest['releaseId']))
        self._rate_limit(response)
        if response.status != 200:
            raise CutError('UPDATE_WITHDRAWN', 'Selected release is no longer available')
        release = _json(response.body)
        if (release.get('id') != manifest['releaseId'] or release.get('tag_name') != manifest['tag']
                or release.get('name') != 'Contentrium CUT' or release.get('draft') is not False
                or release.get('prerelease') is not False):
            raise CutError('UPDATE_WITHDRAWN', 'Selected release identity or stable status changed')
        fields = ('id', 'name', 'size', 'state', 'browser_download_url')
        def metadata(assets):
            indexed = {}
            for asset in assets:
                if asset['id'] in indexed:
                    raise CutError('UPDATE_ASSET', 'Duplicate selected release asset ID')
                indexed[asset['id']] = tuple(asset[field] for field in fields)
                validate_url(asset['browser_download_url'])
            return indexed
        if metadata(release['assets']) != metadata(pinned_release['assets']):
            raise CutError('UPDATE_WITHDRAWN', 'Selected release assets changed since verification')
        pinned = self._state['_attemptCandidate']
        self._verify_manifest(base64.b64decode(pinned['raw']), base64.b64decode(pinned['signature']), release)
        self._commit(releaseRevalidatedAt=self.clock(), error=None)

    def _prepare(self, manifest, release):
        stage = self.updates / self._state['updateId']
        stage.mkdir(exist_ok=True)
        needed = sum(asset['size'] for asset in manifest['assets']) * 2
        if shutil.disk_usage(stage).free < needed:
            raise CutError('UPDATE_SPACE', 'Insufficient update staging space')
        prepared = {}
        for asset in manifest['assets']:
            self._cancel_check()
            target = stage / asset['name']
            partial = target.with_name(target.name + '.part')
            partial.unlink(missing_ok=True)
            actual = next(item for item in release['assets'] if item['id'] == asset['assetId'])
            try:
                self.transport.download(validate_url(actual['browser_download_url']), partial, dict(BINARY_HEADERS),
                                        self._cancel_check, asset['size'])
                self._cancel_check()
                digest = hashlib.sha256()
                with open(partial, 'rb') as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        self._cancel_check()
                        digest.update(chunk)
                if partial.stat().st_size != asset['size'] or digest.hexdigest() != asset['sha256']:
                    raise CutError('UPDATE_HASH', 'Payload does not match signed hash')
                os.replace(partial, target)
            finally:
                partial.unlink(missing_ok=True)
            prepared[asset['role']] = {'path': str(target), 'sha256': asset['sha256'], 'assetId': asset['assetId']}
        self._commit(updateState='VERIFYING_PACKAGE', preparedAssets=prepared)
        for entry in prepared.values():
            if Path(entry['path']).suffix.lower() in {'.zip', '.ccx'}:
                self._cancel_check()
                validate_zip(entry['path'])
        return prepared

    def advance(self):
        if not self._operation.acquire(blocking=False):
            return self.state()
        replaced = False
        try:
            phase = self._state['updateState']
            if phase not in PRE_REPLACE:
                return self.state()
            if self._state.get('stopSignaled') is not True:
                return self.state()
            if self._state['retryAt'] > self.clock():
                return self.state()
            if not self._quiesced():
                self._commit(updateState='QUIESCING')
                return self.state()
            self._cancel_check()
            manifest, release = self._candidate()
            if not self._state.get('preparedAssets'):
                self._commit(updateState='DOWNLOADING')
                prepared = self._prepare(manifest, release)
                self._commit(updateState='WAITING_HOST_EXIT', preparedAssets=prepared)
            else:
                self._commit(updateState='WAITING_HOST_EXIT')
            if not self.installation.host_exited():
                return self.state()
            if not self._quiesced():
                return self.state()
            self._cancel_check()
            self._confirm_pinned_release(manifest, release)
            self._cancel_check()
            self._commit(snapshotIntent=True)
            snapshot = self.installation.snapshot(manifest)
            if (not isinstance(snapshot, dict) or snapshot.get('previousVerified') is not True
                    or snapshot.get('previousVersion') != self._state['oldVersion']
                    or not isinstance(snapshot.get('dataSnapshot'), (str, dict)) or not snapshot['dataSnapshot']):
                raise CutError('UPDATE_ROLLBACK_UNAVAILABLE', 'Previous application and data recovery point could not be verified')
            self._commit(snapshot=snapshot)
            self._cancel_check()
            paths = self._verify_prepared(manifest)
            with self._lock:
                self._cancel_check()
                if not self._quiesced():
                    self._commit(updateState='QUIESCING')
                    return self.state()
                if not self.installation.host_exited():
                    return self.state()
                self._commit(updateState='INSTALLING')  # Persist intent before replacement.
                replaced = True
            result = self.installation.install(manifest, paths, snapshot)
            if not isinstance(result, dict):
                raise CutError('UPDATE_INSTALL_RESULT', 'Installation hook did not return verified result metadata')
            self._commit(updateState='PENDING_ACTIVATION', installerResult=result)
            if self._canceled.is_set():
                self._rollback(CutError('CANCELED', 'Update canceled after replacement'))
        except Exception as error:
            if replaced:
                self._rollback(error)
            elif getattr(error, 'code', None) == 'UPDATE_RATE_LIMIT':
                self._commit(updateState='WAITING_HOST_EXIT', gateOpen=False, error=self._error(error))
            else:
                self._finish_before_replace(error)
        finally:
            self._operation.release()
        return self.state()

    def _verify_prepared(self, manifest):
        paths = {}
        for asset in manifest['assets']:
            entry = self._state['preparedAssets'][asset['role']]
            expected = self.updates / self._state['updateId'] / asset['name']
            path = Path(entry['path'])
            if path != expected or path.is_symlink() or path.resolve() != expected:
                raise CutError('UPDATE_PATH', 'Staged asset path changed')
            digest = hashlib.sha256()
            with open(path, 'rb') as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    self._cancel_check()
                    digest.update(chunk)
            if path.stat().st_size != asset['size'] or digest.hexdigest() != asset['sha256']:
                raise CutError('UPDATE_HASH', 'Staged asset changed before installation')
            paths[asset['role']] = path
        return paths

    def _finish_before_replace(self, error):
        phase = 'CANCELED' if getattr(error, 'code', None) == 'CANCELED' else 'FAILED_BEFORE_REPLACE'
        snapshot = self._state.get('snapshot') or {'previousVersion': self._state.get('oldVersion', self.current_version)}
        try:
            verified = bool(self.installation.verify_previous(snapshot))
            self._commit(updateState=phase if verified else 'RECOVERY_REQUIRED', gateOpen=verified,
                         previousVerification={'verified': verified, 'version': snapshot['previousVersion'], 'checkedAt': self.clock()},
                         error=self._error(error))
        except Exception:
            self._blocked = True
            self._commit(updateState='RECOVERY_REQUIRED', gateOpen=False, error=self._error(error))

    def _rollback(self, error):
        self._blocked = True
        try:
            self._commit(updateState='ROLLING_BACK', gateOpen=False, error=self._error(error))
            snapshot = self._state.get('snapshot')
            if not snapshot or snapshot.get('previousVerified') is not True:
                raise CutError('UPDATE_RECOVERY', 'Verified recovery snapshot is unavailable')
            result = self.installation.rollback(snapshot)
            if not isinstance(result, dict):
                raise CutError('UPDATE_ROLLBACK_RESULT', 'Rollback hook did not return result metadata')
            if not self.installation.verify_previous(snapshot):
                raise CutError('UPDATE_RECOVERY', 'Previous application could not be verified')
            self._commit(updateState='ROLLED_BACK', gateOpen=True, rollbackResult=result,
                         currentVersion=snapshot['previousVersion'],
                         previousVerification={'verified': True, 'version': snapshot['previousVersion'], 'checkedAt': self.clock()})
            self.current_version = snapshot['previousVersion']
        except Exception as rollback_error:
            try:
                self._commit(updateState='RECOVERY_REQUIRED', gateOpen=False, error=self._error(rollback_error))
            except Exception:
                # The prior durable intent still requires recovery; never invent success.
                self._blocked = True

    def activate(self, receipt):
        with self._operation:
            if self._state['updateState'] not in {'PENDING_ACTIVATION', 'VERIFYING_INSTALL'}:
                raise CutError('UPDATE_ACTIVATION', 'No installation awaits activation')
            try:
                version, bundle = self._state['newVersion'], self._state['bundleId']
                self._commit(updateState='VERIFYING_INSTALL')
                if (receipt.get('panelVersion') != version or receipt.get('companionVersion') != version
                        or receipt.get('bundleId') != bundle or receipt.get('handshake') is not True
                        or receipt.get('dataReadable') is not True
                        or not self.installation.verify(version, bundle, receipt)):
                    raise CutError('UPDATE_ACTIVATION', 'Loaded panel and Companion were not verified as one bundle')
                self._commit(updateState='COMPLETE', gateOpen=True, activationReceipt=receipt,
                             activationVerified=True, currentVersion=version, candidate=None, checkState='CURRENT',
                             _verifiedRelease={'checkState': 'CURRENT', 'candidate': None})
                self.current_version = version
            except Exception as error:
                self._rollback(error)
            return self.state()

    def cancel(self):
        with self._lock:
            if self._state['updateState'] in TERMINAL:
                return self.state()
            self._canceled.set()
            self._commit(cancelRequested=True)
            if self._stop_inflight is not None:
                return self.state()
        if self._operation.acquire(blocking=False):
            try:
                if self._state['updateState'] in PRE_REPLACE:
                    self._finish_before_replace(CutError('CANCELED', 'Update canceled'))
                elif self._state['updateState'] in {'PENDING_ACTIVATION', 'VERIFYING_INSTALL', 'INSTALLING'}:
                    self._rollback(CutError('CANCELED', 'Update canceled after replacement'))
            finally:
                self._operation.release()
        return self.state()

    def recover(self):
        """Explicitly verify/restore a durable interrupted install, never resume cuts."""
        with self._operation:
            if self._state['updateState'] != 'RECOVERY_REQUIRED':
                return self.state()
            if self._stop_inflight is not None:
                return self.state()
            if self._journal_bad:
                return self.state()
            snapshot = self._state.get('snapshot')
            if snapshot and snapshot.get('previousVerified') is True:
                self._rollback(CutError('UPDATE_INTERRUPTED', 'Recovering interrupted update'))
            else:
                self._finish_before_replace(CutError('UPDATE_INTERRUPTED', 'Verifying previous installation'))
            return self.state()
