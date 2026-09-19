"""Transactional publication of an export request (quality remedy M2B, hardened in 0.13 H1).

Every file of a request (the maps of every set, manifests, the mesh, a bundle's files) is
generated into staging first. Publication is one request-level transaction with a durable
decision record:

    PREPARED -> (publishing) -> COMMITTED -> cleanup complete (journal removed)

1. The participating folders are locked in sorted order (``LOCK`` files owned by this
   process; a stale lock of a dead process is broken, a live one refuses with ExportBusy),
   and any interrupted earlier transaction in them is recovered first, under the locks.
2. Destinations are validated: no duplicates; without ``overwrite`` none may exist.
3. A journal is written atomically into every participating folder (``JOURNAL``): the
   request id, all folders, the coordinator journal (the first folder's), and per item its
   name, whether the destination was absent, the original's size/mtime/sha256, the backup
   and staging names, and the sha256 of the staged bytes. Journals are written through a
   temporary file, fsync'd and renamed, with the directory fsync'd where the platform allows.
4. Existing destinations are renamed to backups; each staged file is copied beside its
   destination (flushed) and moved into place — ``os.replace`` when replacing, otherwise an
   atomic no-clobber hard link (``os.link``) with an exclusive-create copy as the fallback
   for filesystems without hard links. A competing file that appeared at the destination
   is never overwritten: the request rolls back and reports the conflict.
5. Once every output is in place, COMMITTED is written to the coordinator journal, then to
   the others, before any backup is removed. From then on the new outputs are the result:
   cleanup (backups, journals) is retryable housekeeping; a failure there returns
   ``Outcome.cleanup_pending`` and leaves the journals for the next recovery.

``recover(folder)`` reads the folder's journal, validates it (format, id, names inside the
folder), consults the coordinator's decision and applies it to every participating folder:
pre-commit → remove the files this transaction published (recognised by the recorded
digest), put backups back; post-commit → remove backups and staging leftovers, keep every
new output. A file whose bytes are neither the original's nor ours is foreign: it is kept
and reported; an unreadable or invalid journal, a missing coordinator or a conflict raises
RecoveryRequired with the evidence (journal, backups) left in place. Recovery is
idempotent and refuses to touch a transaction whose owner process is alive.

Tested scope: local APFS (macOS) with process interruption at every boundary
(tests/unit/test_export_transaction.py). Power-loss durability and network filesystems
are not established by those tests.
"""
import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

JOURNAL = '.open_painter_export_journal.json'
LOCK = '.open_painter_export.lock'
FORMAT = 2
BACKUP_SUFFIX, STAGING_SUFFIX = '.op-backup-', '.op-staging-'
FAILPOINT_VARIABLE = 'OP_EXPORT_FAILPOINT'
FAULT_VARIABLE = 'OP_EXPORT_FAULT'


class ExportBusy(RuntimeError):
    """Another export is publishing into one of the folders."""


class RecoveryRequired(RuntimeError):
    """Recovery could not complete safely; the journal and backups are kept and named."""


class PublicationConflict(RuntimeError):
    """A destination was taken by another writer; nothing of this request was published."""


def failpoint(name):
    """A hard termination at a boundary (tests): production ordering is unchanged."""
    if os.environ.get(FAILPOINT_VARIABLE) == name:
        os._exit(3)


def _fault(name):
    """A catchable failure at a boundary (tests), raised as OSError."""
    if os.environ.get(FAULT_VARIABLE) == name:
        raise OSError(f'injected fault at {name}')


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(folder):
    if os.name != 'posix':
        return
    try:
        fd = os.open(str(folder), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_json(path, data):
    """Atomic, durable JSON write: temporary file, fsync, rename, directory fsync."""
    temporary = path.with_name(path.name + '.tmp')
    with open(temporary, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, indent=1)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _copy_flushed(source, target):
    with open(source, 'rb') as inp, open(target, 'wb') as out:
        shutil.copyfileobj(inp, out, 1 << 20)
        out.flush()
        os.fsync(out.fileno())


def _copy_exclusive(source, target):
    """Create ``target`` exclusively and fill it: the fallback no-clobber publication where
    hard links are unavailable (the create is exclusive; the bytes follow)."""
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with open(source, 'rb') as inp, os.fdopen(fd, 'wb') as out:
        shutil.copyfileobj(inp, out, 1 << 20)
        out.flush()
        os.fsync(out.fileno())


def _publish_no_clobber(staging, destination):
    """Move the completed sibling ``staging`` to ``destination`` without ever replacing an
    existing file. Returns the method used. Raises FileExistsError when the destination
    exists (a competing writer)."""
    try:
        os.link(staging, destination)
    except FileExistsError:
        raise
    except OSError as error:
        # No hard links here (some network or FAT volumes): exclusive create, then fill.
        import errno
        if error.errno not in {errno.EPERM, errno.ENOTSUP, getattr(errno, 'EOPNOTSUPP', errno.ENOTSUP), errno.EMLINK, errno.ENOSYS}:
            raise
        _copy_exclusive(staging, destination)
        os.unlink(staging)
        return 'exclusive-create'
    os.unlink(staging)
    return 'hard-link'


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class _Lock:
    """An exclusive lock on one folder, owned by this process (pid inside the lock file)."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.path = self.folder / LOCK
        self.held = False

    def acquire(self):
        for _ in range(3):
            try:
                fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                owner = self._owner()
                if owner is not None and (owner == os.getpid() or _pid_alive(owner)):
                    raise ExportBusy(f'another export is publishing into {self.folder} (process {owner}); wait for it to finish')
                # The owner is gone: the lock is stale.
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(fd, 'w') as stream:
                stream.write(f'{os.getpid()}\n{time.time():.3f}\n')
            self.held = True
            return self
        raise ExportBusy(f'could not lock {self.folder}')

    def _owner(self):
        try:
            return int(self.path.read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None

    def release(self):
        if self.held:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self.held = False


_held_locks = {}   # folder (resolved str) -> _Lock held by this process (re-entrant within a process)


def _lock_folders(folders):
    """Lock folders in sorted order; folders this process already holds are reused."""
    acquired = []
    for folder in sorted({str(Path(f).resolve()) for f in folders}):
        if folder in _held_locks:
            continue
        lock = _Lock(folder)
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
            lock.acquire()
        except BaseException:
            for held in acquired:
                held.release()
                _held_locks.pop(str(held.folder), None)
            raise
        _held_locks[folder] = lock
        acquired.append(lock)
    return acquired


def _unlock(locks):
    for lock in reversed(locks):
        lock.release()
        _held_locks.pop(str(lock.folder), None)


@dataclass
class Item:
    destination: Path
    source: Path                 # the staged file
    absent: bool = True          # no destination existed when the request was prepared
    original: dict = None        # size, mtime_ns, sha256 of the destination that existed
    backup: Path = None
    staging: Path = None
    staged_sha256: str = ''
    published_sha256: str = ''
    method: str = ''

    def record(self):
        return {'name': self.destination.name, 'folder': str(self.destination.parent), 'absent': self.absent,
                'original': self.original, 'backup': self.backup.name if self.backup else None,
                'staging': self.staging.name if self.staging else None, 'staged_sha256': self.staged_sha256,
                'published_sha256': self.published_sha256 or None, 'method': self.method or None}


@dataclass
class Outcome:
    destinations: list
    cleanup_pending: list = field(default_factory=list)
    message: str = 'Export completed'

    def __iter__(self):
        return iter(self.destinations)

    def __len__(self):
        return len(self.destinations)


@dataclass
class Transaction:
    items: list = field(default_factory=list)
    identifier: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def add(self, destination, source):
        self.items.append(Item(Path(destination), Path(source)))

    @property
    def destinations(self):
        return [item.destination for item in self.items]

    @property
    def folders(self):
        return sorted({str(item.destination.parent.resolve()) for item in self.items}) if self.items else []

    def validate(self, overwrite):
        seen = {}
        for item in self.items:
            if not item.destination.name or item.destination.name in {'.', '..'} or os.sep in item.destination.name:
                raise ValueError(f'invalid destination name: {item.destination}')
            key = str(item.destination.resolve())
            if key in seen:
                raise ValueError(f'Two outputs would write the same file: {item.destination.name}')
            seen[key] = item
            if not item.source.is_file():
                raise FileNotFoundError(f'Staged file missing: {item.source}')
        if not overwrite:
            existing = [item.destination.name for item in self.items if item.destination.exists()]
            if existing:
                raise ValueError('Export files already exist: ' + ', '.join(existing[:5])
                                 + (' …' if len(existing) > 5 else '') + '. Choose another folder, prefix or suffix, or allow overwriting.')

    def _journal_record(self, state):
        return {'format': FORMAT, 'transaction': self.identifier, 'state': state, 'pid': os.getpid(),
                'created': time.time(), 'coordinator': str(self._coordinator), 'folders': self.folders,
                'items': [item.record() for item in self.items]}

    def _write_journals(self, state, coordinator_first):
        record = self._journal_record(state)
        order = self.folders if coordinator_first else list(reversed(self.folders))
        for folder in order:
            _write_json(Path(folder) / JOURNAL, record)

    def commit(self, overwrite=False):
        """Publish every item as one request. Returns an Outcome; raises on refusal or failure
        (with the pre-request state restored, or RecoveryRequired naming the evidence)."""
        self.validate(overwrite)
        if not self.items:
            return Outcome([])
        folders = self.folders
        self._coordinator = Path(folders[0]) / JOURNAL
        locks = _lock_folders(folders)
        try:
            for folder in folders:
                recover(folder)              # an interrupted earlier request first, under the locks
            self.validate(overwrite)         # recovery may have restored files this request must not replace
            for item in self.items:
                item.staging = item.destination.with_name(f'{item.destination.name}{STAGING_SUFFIX}{self.identifier}')
                item.staged_sha256 = _sha256(item.source)
                if item.destination.exists():
                    item.absent = False
                    stat = item.destination.stat()
                    item.original = {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'sha256': _sha256(item.destination)}
                    item.backup = item.destination.with_name(f'{item.destination.name}{BACKUP_SUFFIX}{self.identifier}')
            self._write_journals('PREPARED', coordinator_first=True)
            failpoint('after_prepared')
            published, moved = [], []
            try:
                for item in self.items:
                    if item.backup is not None:
                        _fault('backup')
                        os.replace(item.destination, item.backup)
                        moved.append(item)
                        failpoint('after_first_backup') if len(moved) == 1 else None
                failpoint('after_backups')
                for item in self.items:
                    _fault('publish' if item is self.items[-1] else '')
                    _copy_flushed(item.source, item.staging)
                    if overwrite:
                        os.replace(item.staging, item.destination)
                        item.method = 'replace'
                    else:
                        try:
                            item.method = _publish_no_clobber(item.staging, item.destination)
                        except FileExistsError:
                            raise PublicationConflict(
                                f'{item.destination.name} was created by another writer while this export ran; '
                                f'nothing was replaced (allow overwriting to replace it)')
                    item.published_sha256 = item.staged_sha256
                    published.append(item)
                    if len(published) == 1:
                        failpoint('after_first_publish')
                failpoint('before_commit_decision')
                # The durable decision: the coordinator's COMMITTED record, before any backup goes.
                _write_json(self._coordinator, self._journal_record('COMMITTED'))
                failpoint('after_commit_decision')
                for folder in folders[1:]:
                    _write_json(Path(folder) / JOURNAL, self._journal_record('COMMITTED'))
            except BaseException as failure:
                problems = self._rollback(published, moved)
                if problems:
                    raise RecoveryRequired(
                        f'{failure}; restoring the previous export also failed for {"; ".join(problems)} — the journal '
                        f'{JOURNAL} and the backups stay in {folders[0]} for recovery') from failure
                self._remove_journals()
                raise
            # Committed: what follows is housekeeping.
            pending = []
            for index, item in enumerate(self.items):
                if item.backup is not None and item.backup.exists():
                    try:
                        _fault('cleanup' if index == 1 else '')
                        item.backup.unlink()
                    except OSError as error:
                        pending.append(f'{item.backup.name}: {error}')
                    if index == 0:
                        failpoint('after_first_backup_removed')
                for leftover in (item.staging,):
                    if leftover is not None and leftover.exists():
                        try:
                            leftover.unlink()
                        except OSError as error:
                            pending.append(f'{leftover.name}: {error}')
            if not pending:
                self._remove_journals()
            message = 'Export completed' if not pending else (
                'Export completed; cleanup pending (the next export or recovery finishes it): ' + '; '.join(pending))
            return Outcome(self.destinations, pending, message)
        finally:
            _unlock(locks)

    def _remove_journals(self):
        # Participants first, the coordinator (the decision) last.
        for folder in reversed(self.folders):
            try:
                (Path(folder) / JOURNAL).unlink()
            except FileNotFoundError:
                pass

    def _rollback(self, published, moved):
        """Undo a pre-commit failure: remove what this request published (verified by digest),
        put backups back where the destination is free. Returns the problems it could not fix."""
        problems = []
        for item in published:
            try:
                if item.destination.exists():
                    if _sha256(item.destination) == item.published_sha256:
                        item.destination.unlink()
                    else:
                        problems.append(f'{item.destination.name} was changed by another writer since this export published it')
            except OSError as error:
                problems.append(f'{item.destination.name}: {error}')
        for item in self.items:
            if item.staging is not None and item.staging.exists():
                try:
                    item.staging.unlink()
                except OSError:
                    pass
        for item in moved:
            try:
                if item.backup.exists():
                    if item.destination.exists():
                        problems.append(f'{item.destination.name} is occupied by another writer; its previous version stays in {item.backup.name}')
                    else:
                        os.replace(item.backup, item.destination)
            except OSError as error:
                problems.append(f'{item.destination.name} (backup {item.backup.name}): {error}')
        return problems


# ---- recovery -----------------------------------------------------------------------------------

def _load_journal(folder):
    """The folder's journal as a validated record, or None when there is none.
    Raises RecoveryRequired for an unreadable or invalid one (kept in place)."""
    folder = Path(folder).resolve()
    journal = folder / JOURNAL
    if not journal.exists():
        return None
    try:
        data = json.loads(journal.read_text(encoding='utf-8'))
    except (ValueError, OSError) as error:
        raise RecoveryRequired(f'the export journal {journal} cannot be read ({error}); it and any '
                               f'{BACKUP_SUFFIX.strip(".")}* files in {folder} are kept for recovery') from error
    if not isinstance(data, dict) or data.get('format') != FORMAT:
        raise RecoveryRequired(f'the export journal {journal} has an unsupported format ({data.get("format") if isinstance(data, dict) else "?"}); '
                               f'kept for recovery')
    identifier = data.get('transaction')
    if not isinstance(identifier, str) or not identifier.isalnum() or not (8 <= len(identifier) <= 32):
        raise RecoveryRequired(f'the export journal {journal} names an invalid transaction; kept for recovery')
    if data.get('state') not in {'PREPARED', 'COMMITTED'}:
        raise RecoveryRequired(f'the export journal {journal} is in an unknown state {data.get("state")!r}; kept for recovery')
    folders = data.get('folders')
    if not isinstance(folders, list) or not folders or str(folder) not in {str(Path(f).resolve()) for f in folders}:
        raise RecoveryRequired(f'the export journal {journal} does not list its own folder; kept for recovery')
    items = data.get('items')
    if not isinstance(items, list):
        raise RecoveryRequired(f'the export journal {journal} lists no items; kept for recovery')
    for item in items:
        if not isinstance(item, dict):
            raise RecoveryRequired(f'the export journal {journal} has a malformed item; kept for recovery')
        name = item.get('name')
        if not isinstance(name, str) or not name or '/' in name or os.sep in name or name in {'.', '..'}:
            raise RecoveryRequired(f'the export journal {journal} has an invalid item name {name!r}; kept for recovery')
        try:
            item_folder = Path(item.get('folder', '')).resolve()
        except (TypeError, OSError):
            raise RecoveryRequired(f'the export journal {journal} has an invalid item folder; kept for recovery')
        if str(item_folder) not in {str(Path(f).resolve()) for f in folders}:
            raise RecoveryRequired(f'the export journal {journal} places {name} outside the request folders; kept for recovery')
        for key, suffix in (('backup', BACKUP_SUFFIX), ('staging', STAGING_SUFFIX)):
            value = item.get(key)
            if value is not None and value != f'{name}{suffix}{identifier}':
                raise RecoveryRequired(f'the export journal {journal} has an unexpected {key} name for {name}; kept for recovery')
    data['_path'] = journal
    return data


def recover(folder):
    """Finish or undo an interrupted publication that left a journal in ``folder`` (and in the
    other folders of the same request). Returns a list of what was done; [] without a
    journal. Raises RecoveryRequired when evidence must be kept, ExportBusy when the
    transaction's owner is still running. Safe to call repeatedly."""
    folder = Path(folder).resolve()
    data = _load_journal(folder)
    if data is None:
        return []
    owner = data.get('pid')
    if isinstance(owner, int) and owner != os.getpid() and _pid_alive(owner):
        lock = folder / LOCK
        if lock.exists():
            raise ExportBusy(f'the export into {folder} (process {owner}) is still running; nothing to recover yet')
    coordinator_path = Path(data['coordinator'])
    if coordinator_path.resolve() == data['_path'].resolve():
        decision = data
    else:
        try:
            decision = _load_journal(coordinator_path.parent)
        except RecoveryRequired:
            raise
        if decision is None or decision.get('transaction') != data['transaction']:
            raise RecoveryRequired(f'the decision record of export {data["transaction"]} ({coordinator_path}) is missing; '
                                   f'the journal and backups in {folder} are kept until it is found')
    folders = [Path(f).resolve() for f in decision['folders']]
    locks = _lock_folders([str(f) for f in folders])
    try:
        return _apply_decision(decision, folders)
    finally:
        _unlock(locks)


def _apply_decision(decision, folders):
    identifier = decision['transaction']
    committed = decision['state'] == 'COMMITTED'
    actions, conflicts = [], []
    for item in decision['items']:
        folder = Path(item['folder']).resolve()
        destination = folder / item['name']
        staging = folder / item['staging'] if item.get('staging') else None
        backup = folder / item['backup'] if item.get('backup') else None
        original = item.get('original') or {}
        if staging is not None and staging.exists():
            try:
                staging.unlink()
                actions.append(f'removed staging {staging.name}')
            except OSError as error:
                conflicts.append(f'{staging.name}: {error}')
        if committed:
            if backup is not None and backup.exists():
                try:
                    backup.unlink()
                    actions.append(f'removed backup {backup.name}')
                except OSError as error:
                    conflicts.append(f'{backup.name}: {error}')
            if not destination.exists():
                actions.append(f'note: committed output {destination.name} is missing (removed by another writer)')
            continue
        # Pre-commit: undo what this transaction did, and only that.
        ours = item.get('staged_sha256') or ''
        if destination.exists():
            current = _sha256(destination)
            if current == ours:
                try:
                    destination.unlink()
                    actions.append(f'removed unpublished {destination.name}')
                except OSError as error:
                    conflicts.append(f'{destination.name}: {error}')
                    continue
            elif backup is not None and backup.exists():
                if current != original.get('sha256'):
                    conflicts.append(f'{destination.name} holds another writer\'s file; the previous version stays in {backup.name}')
                    continue
                # The original is back at its place already (an earlier recovery): the backup is a duplicate.
                try:
                    backup.unlink()
                    actions.append(f'removed duplicate backup {backup.name}')
                except OSError as error:
                    conflicts.append(f'{backup.name}: {error}')
                continue
            elif item.get('absent'):
                conflicts.append(f'{destination.name} was created by another writer; it is kept')
                continue
            else:
                continue   # the untouched original (its backup was never made, or is already back)
        if backup is not None and backup.exists():
            try:
                os.replace(backup, destination)
                actions.append(f'restored {destination.name}')
            except OSError as error:
                conflicts.append(f'{destination.name} (backup {backup.name}): {error}')
    if conflicts:
        raise RecoveryRequired(f'recovery of export {identifier} needs attention: ' + '; '.join(conflicts)
                               + f'; the journal(s) and backups are kept in {", ".join(str(f) for f in folders)}')
    # Complete: participants' journals first, the coordinator (the decision) last.
    coordinator = Path(decision['coordinator']).resolve()
    for folder in sorted(folders, key=lambda f: f == coordinator.parent):
        journal = folder / JOURNAL
        if journal.exists() and journal.resolve() != coordinator:
            journal.unlink()
    if coordinator.exists():
        coordinator.unlink()
    actions.append('committed outputs kept' if committed else 'previous export restored')
    return actions
