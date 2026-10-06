"""One-time headless source-assisted 0.1.0 -> 0.1.1 migration.

This tool cannot attest native activation. It uses the saved public trust key,
the existing updater journal, B2 preparation and B1's private pipe handoff.
"""
import argparse
import base64
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import uuid

from contentrium_cut.bootstrap import canonical_root, current_user_sid, resolve_installation
from contentrium_cut.contract import CutError
from contentrium_cut.installer_preparation import prepare_installation
from contentrium_cut.integration import install_integration
from contentrium_cut import launcher_transaction as launcher
from contentrium_cut.migration_registration import prepare as prepare_registration
from contentrium_cut.lifecycle import supervisor_lease, verify_runtime, spawn_verified
from contentrium_cut.updater import UpdateManager, TERMINAL, PRE_REPLACE, _json
from contentrium_cut.windows_install import WindowsInstallation, WindowsNamedMutex, _guard_path


def _leases(root, sid):
    # Supervisor precedes runtime, matching the normal parent/child lifecycle.
    return supervisor_lease(root, sid), WindowsNamedMutex(root)


def _read(path):
    _guard_path(path)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise CutError('MAINTENANCE_EVIDENCE', 'Saved installation evidence exceeds its bound.')
    return _json(path.read_bytes())


def _archive_journal(root):
    """Preserve exact prior bytes, without replacing the authoritative journal."""
    source = root/'updates/journal.json'; _guard_path(source)
    if not source.exists(): return
    if source.stat().st_size > 16 * 1024 * 1024:
        raise CutError('MAINTENANCE_EVIDENCE', 'Saved journal exceeds its bound.')
    raw = source.read_bytes()
    target = root/'updates/maintenance-history'/hashlib.sha256(raw).hexdigest()/'journal.json'
    _guard_path(target)
    if target.exists():
        if target.read_bytes() != raw:
            raise CutError('MAINTENANCE_HISTORY', 'Prior journal history conflicts; preserve it for recovery.')
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    # A interrupted partial archive is preserved and refused on retry.
    with target.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def _target(manager, pinned, tag, version, commit):
    manifest, reasons = manager._verify_manifest(base64.b64decode(pinned['raw'], validate=True),
        base64.b64decode(pinned['signature'], validate=True), pinned['release'])
    if (manifest['tag'] != tag or manifest['appVersion'] != version
            or pinned['release'].get('target_commitish') != commit
            or pinned['release'].get('name') != 'Contentrium CUT'
            or pinned['release'].get('draft') is not False or pinned['release'].get('prerelease') is not False
            or not any(asset['role'] == 'installer' for asset in manifest['assets'])):
        raise CutError('MAINTENANCE_TARGET', 'The signed candidate is not the exact requested migration release.')
    if reasons:
        raise CutError('UPDATE_COMPATIBILITY', 'The pinned migration is incompatible.', reasons)
    return manifest


def _legacy(manager, root, config, installation):
    """Authenticate original saved release and unchanged old runtime evidence."""
    first = _read(root/'updates/first-install.json')
    if (first.get('release', {}).get('name') != 'Contentrium CUT'
            or first['release'].get('draft') is not False or first['release'].get('prerelease') is not False):
        raise CutError('MAINTENANCE_LEGACY', 'The original published stable release evidence is required.')
    manifest, _ = manager._verify_manifest(base64.b64decode(first['manifest'], validate=True),
        base64.b64decode(first['signature'], validate=True), first['release'])
    if (manifest['appVersion'] != '0.1.0' or first.get('appVersion') != '0.1.0'
            or first.get('bundleId') != manifest['bundleId'] or config.get('productId') != 'com.contentrium.cut'):
        raise CutError('MAINTENANCE_LEGACY', 'The original signed 0.1.0 installation is required.')
    receipt = installation._verify_directory(root/'app/versions/0.1.0', '0.1.0', manifest['bundleId'])
    if receipt['assets'] != manifest['assets']:
        raise CutError('MAINTENANCE_LEGACY', 'Original installed assets differ from the saved signed release.')
    return manifest


def migrate(root, *, tag, version, commit, action='start', installation=None, transport=None,
            owner_sid=None, roaming_root=None, protect=None, registry=None, menu_root=None,
            lease_factory=None, popen=None, wait=None, cancel=None, clock=time.time, progress=None):
    """Execute one owned stage; injected boundaries are for isolated source tests.

    Without wait, return durable pending status immediately. With wait(seconds),
    a true return or cancel() interrupts waiting, retaining the updater attempt.
    resume is only preparation; recover delegates to the existing rollback path.
    """
    if (tag != 'v0.1.1' or version != '0.1.1' or not isinstance(commit, str)
            or not re.fullmatch('[0-9a-f]{40}', commit) or action not in {'start','resume','recover'}):
        raise CutError('MAINTENANCE_TARGET', 'Require v0.1.1, version 0.1.1 and its exact lowercase full commit SHA.')
    root = canonical_root(root)
    sid = owner_sid or current_user_sid()
    notify = progress or (lambda status: None)
    packet = location = verified = None
    with ExitStack() as ownership:
        for lease in (lease_factory or _leases)(root, sid): ownership.enter_context(lease)
        config = _read(root/'launcher-config.json')
        installed = installation or WindowsInstallation(root, migration_registry=registry)
        manager = UpdateManager(root, config['publicKey'], '0.1.0', lambda epoch: None, installed,
            transport=transport, signing_key_id=config.get('signingKeyId','contentrium-cut-2026-01'),
            participant_exited=lambda _, identity: installed.host_identity_exited(identity), clock=clock)
        legacy = _legacy(manager, root, config, installed)
        state = manager.state()
        if manager._journal_bad:
            raise CutError('UPDATE_JOURNAL', 'The existing journal requires explicit verified repair; it was preserved.')
        if (root/'install-location.json').exists() or (root/'private/auth-bootstrap.json').exists():
            resolve_installation(root, owner_sid=owner_sid)
        if action == 'start':
            if state['updateState'] not in TERMINAL or state['currentVersion'] != '0.1.0':
                raise CutError('MAINTENANCE_RECOVERY', 'Existing update requires explicit recovery or preparation resume.')
            owned = launcher.ownership(root)
            if (not owned['present'] or owned['appVersion'] != '0.1.0'
                    or installed.verify_application('0.1.0', legacy['bundleId']) is not True):
                raise CutError('MAINTENANCE_LEGACY', 'The current original installation could not be verified.')
            _archive_journal(root)
            checked = manager.check()
            if checked['checkState'] != 'AVAILABLE' or not checked['candidate']:
                raise CutError('MAINTENANCE_CANDIDATE', 'No verified compatible migration candidate is available.')
            manifest = _target(manager, manager._state['_candidate'], tag, version, commit)
            candidate = checked['candidate']
            manager.start(candidate['candidateId'], candidate['manifestDigest'], 'source-migration-'+uuid.uuid4().hex)
            attempt = manager.state()
            manager._commit(sourceAssistedMigration=dict(method='source-assisted-0.1.0-to-0.1.1',
                entry='tools.maintenance_entry', tag=tag, appVersion=version, targetCommit=commit,
                updateId=attempt['updateId'], updateEpoch=attempt['updateEpoch']),
                sourceAssistedPreparation=None, sourceAssistedIntegration=None)
            while manager.state()['updateState'] in PRE_REPLACE:
                state = manager.advance(); notify(state['updateState'])
                if state['updateState'] not in PRE_REPLACE: break
                if wait is None or (cancel and cancel()) or wait(0.75): return state
        else:
            if state['updateState'] != 'RECOVERY_REQUIRED' or not manager._state.get('_attemptCandidate'):
                raise CutError('MAINTENANCE_RECOVERY', 'No interrupted pinned update is available for this action.')
            manifest = _target(manager, manager._state['_attemptCandidate'], tag, version, commit)
            if state.get('oldVersion') != '0.1.0':
                raise CutError('MAINTENANCE_LEGACY', 'This attempt is not the original legacy migration.')
            if action == 'recover':
                _archive_journal(root)
                return manager.recover()
            if not launcher.ownership(root)['present']:
                raise CutError('MAINTENANCE_LEGACY', 'Signed installed Launcher ownership is required.')
            _archive_journal(root)
            manager.resume_preparation(state['candidateId'], state['manifestDigest'])
            manager._commit(sourceAssistedMigration=dict(method='source-assisted-0.1.0-to-0.1.1',
                entry='tools.maintenance_entry', tag=tag, appVersion=version, targetCommit=commit,
                updateId=state['updateId'], updateEpoch=state['updateEpoch']))
        state = manager.state()
        if state['updateState'] != 'PENDING_ACTIVATION': return state
        target_config = _read(root/'launcher-config.json')
        if (target_config.get('appVersion') != version or target_config.get('bundleId') != manifest['bundleId']
                or target_config.get('publicKey') != config['publicKey']):
            raise CutError('MAINTENANCE_TARGET', 'Installed integration descriptor differs from the exact signed target.')
        options = dict(owner_sid=owner_sid, roaming_root=roaming_root)
        if protect is not None: options['protect'] = protect
        read_grace = 0
        while True:
            try:
                prepared = prepare_installation(root, target_config, installation=installed, clock=clock, **options)
            except CutError as error:
                if error.code != 'INSTALL_RECEIPT_INCOMPLETE': raise
                if wait is None or read_grace >= 3:
                    raise CutError('INSTALL_RECEIPT_INVALID', 'Panel enrollment receipt is incomplete; resume the same preparation.') from None
                read_grace += 1
                if (cancel and cancel()) or wait(0.1): return dict(state, status='PENDING_PROVISIONING')
                continue
            if prepared['status'] == 'PREPARED': break
            if prepared['status'] != 'PENDING_PROVISIONING':
                raise CutError('INSTALL_PREPARATION', 'Preparation did not verify the installed target.')
            notify('OPEN_PREMIERE_PANEL')
            if wait is None or (cancel and cancel()) or wait(0.75): return dict(state, **prepared)
        if cancel and cancel(): return dict(state, status='PREPARED')
        location = resolve_installation(root, owner_sid=owner_sid)
        verified = verify_runtime(location, installed)
        prepare_registration(root, manager.state()['snapshot'], registry=registry)
        integration = install_integration(root, target_config, launcher_source=root/launcher.NAME,
            resource_root=verified.executable.parent, registry=registry, menu_root=menu_root,
            mapping_evidence=prepared['mappingEvidence'], popen=popen, launch=False, **options)
        if (integration.get('bootstrapProvisioned') is not True or integration.get('dispatchStarted') is not False):
            raise CutError('INSTALL_INTEGRATION', 'Protected migration integration has not completed.')
        manager._commit(sourceAssistedPreparation=prepared, sourceAssistedIntegration=integration)
        # All operations above are synchronous. Both leases exclude live service
        # owners; this process has no workers/server to drain. After ticket issue
        # it performs no further journal or integration writes.
        ticket = manager.create_activation_handoff()
        packet = dict(token=ticket['token'], updateId=ticket['updateId'])
        state = manager.state()
    # Exactly one immediate launch, only after releasing runtime then supervisor.
    spawn_verified(location, verified, supervisor=True, activation=packet, popen=popen)
    return dict(state, status='HANDOFF_DISPATCHED')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--action', choices=['start','resume','recover'], default='start')
    parser.add_argument('--wait', action='store_true', help='Wait for normal host exit and own-panel preparation; Ctrl+C preserves pending state.')
    args = parser.parse_args(argv)
    try:
        result = migrate(args.root, tag=args.tag, version=args.version, commit=args.commit, action=args.action,
            wait=threading.Event().wait if args.wait else None,
            progress=lambda stage: print(stage, flush=True))
        # Do not print internal paths, saved participants or arbitrary exceptions.
        print(json.dumps({key:result.get(key) for key in ['status','updateState','updateId','gateOpen']}, ensure_ascii=False))
        return 0 if result.get('status') == 'HANDOFF_DISPATCHED' else 2
    except KeyboardInterrupt:
        print('INTERRUPTED: saved state retained; use the documented exact-target resume/recover action.', file=sys.stderr)
        return 130
    except CutError as error:
        print(error.code+': migration stopped; preserve saved state and use the documented recovery action.', file=sys.stderr)
        return 1
    except Exception:
        print('MAINTENANCE_FAILED: migration stopped with saved evidence retained.', file=sys.stderr)
        return 1


if __name__ == '__main__': sys.exit(main())
