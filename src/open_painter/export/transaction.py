"""Transactional publication of an export request (quality remedy M2B, hardened in 0.13 H1,
ownership-proven recovery in 0.13 S1).

Every file of a request (the maps of every set, manifests, the mesh, a bundle's files) is
generated into staging first. Publication is one request-level transaction with a durable
decision record:

    PREPARED -> (staged, backed up, published) -> COMMITTED -> cleanup complete (journal removed)

1. The participating folders are locked in sorted order (``LOCK`` files owned by this
   process; a stale lock of a dead process is broken, a live one refuses with ExportBusy),
   and any interrupted earlier transaction in them is recovered first, under the locks.
2. Destinations are validated: no duplicates; without ``overwrite`` none may exist.
3. A journal is written atomically into every participating folder (``JOURNAL``): the
   request id, all folders, the coordinator journal (the first folder's), and per item its
   name, whether the destination was absent, the original's size/mtime/sha256, the backup
   and staging names, and the sha256 of the staged bytes. Journals are written through a
   temporary file, fsync'd and renamed, with the directory fsync'd where the platform allows.
4. Each staged file is copied beside its destination (flushed) as a *staging sibling*;
   existing destinations are renamed to backups; each sibling is published by an atomic
   no-clobber hard link (``os.link``), or by an exclusive create whose ownership is recorded
   in the coordinator journal before any byte is written, where hard links are unavailable.
   The sibling stays until the request is committed: it is the proof that a destination
   is this request's file (``os.path.samefile``). A competing file that appeared at the
   destination is never overwritten, whatever ``overwrite`` says about files that existed
   when the request was prepared: the request rolls back and reports the conflict.
5. Once every output is in place, COMMITTED is written to the coordinator journal, before
   any backup is removed. From then on the new outputs are the result: participant
   journals, backups, siblings and the journals are retryable housekeeping; a failure there
   returns ``Outcome.cleanup_pending`` and leaves the journals for the next recovery.

``recover(folder)`` reads the folder's journal, validates it (format, id, names inside the
folder), consults the coordinator's decision and applies it to every participating folder.
Post-commit it removes backups and siblings and keeps every new output. Pre-commit it
returns the request to its pre-request state, deciding per file from separate evidence —
whether an original existed, whether a verified backup exists, whether the destination is
provably this request's file, and what the destination's bytes are — never from a digest
alone (equal bytes prove equality, not authorship, and an original may equal its
replacement):

* an original present at its destination with its recorded digest is preserved, whether
  untouched or already restored by an earlier recovery (a surviving backup is then a
  duplicate and goes);
* a destination proven ours (its sibling is the same file, or its exclusive creation is
  claimed in the journal) is removed and the backup, if any, put back;
* a destination that is neither is foreign or unproven: it is kept and reported, as is an
  original that is missing with no backup; RecoveryRequired names the evidence (journal,
  backups) left in place.

Recovery is idempotent (an interrupted recovery converges on the next run), refuses to
touch a transaction whose owner process is alive, and applies one decision to all folders
of a request. Journals of the previous implementation (format 2, siblings removed at
publication) are recovered by the same rules; their published-but-uncommitted files cannot
be proven and are kept with an error rather than deleted.

Tested scope: local APFS (macOS) with process interruption at every boundary, including
inside recovery (tests/unit/test_export_transaction.py). Power-loss durability, network
filesystems and instantaneous atomic visibility of the whole request to unrelated readers
are not established by those tests: the request is coherent and recoverable, not atomic to
a concurrent reader.
"""
import errno
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

JOURNAL = '.open_painter_export_journal.json'
LOCK = '.open_painter_export.lock'
FORMAT = 3
ACCEPTED_FORMATS = {2, 3}
BACKUP_SUFFIX, STAGING_SUFFIX = '.op-backup-', '.op-staging-'
FAILPOINT_VARIABLE = 'OP_EXPORT_FAILPOINT'
FAULT_VARIABLE = 'OP_EXPORT_FAULT'
COPY_CHUNK = 1 << 20


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
    if name and os.environ.get(FAULT_VARIABLE) == name:
        raise OSError(f'injected fault at {name}')


def _sha256(path):
    from ..utils.digest import sha256
    digest = sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(COPY_CHUNK), b''):
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
        shutil.copyfileobj(inp, out, COPY_CHUNK)
        out.flush()
        os.fsync(out.fileno())


def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _pid_alive(pid):
    if os.name == 'nt':
        # Unlike POSIX, os.kill(pid, 0) terminates the process on Windows.
        # A process handle becomes signaled when it exits; a zero-timeout wait
        # observes that state without sending a signal or requesting kill rights.
        import ctypes
        from ctypes import wintypes

        if pid <= 0 or pid > 0xFFFFFFFF:
            return False
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            # Only ERROR_INVALID_PARAMETER proves there is no such process.
            # Access denied and other failures must not let us break its lock.
            return ctypes.get_last_error() != 87
        try:
            return kernel32.WaitForSingleObject(handle, 0) != 0  # WAIT_OBJECT_0
        finally:
            kernel32.CloseHandle(handle)
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
    staging: Path = None         # the sibling copy beside the destination (kept until commit)
    staged_sha256: str = ''
    published_sha256: str = ''
    method: str = ''
    claimed: bool = False        # the destination was created exclusively by this request (fallback)

    def record(self):
        return {'name': self.destination.name, 'folder': str(self.destination.parent), 'absent': self.absent,
                'original': self.original, 'backup': self.backup.name if self.backup else None,
                'staging': self.staging.name if self.staging else None, 'staged_sha256': self.staged_sha256,
                'published_sha256': self.published_sha256 or None, 'method': self.method or None,
                'claimed': self.claimed}


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

    def _publish(self, item):
        """Put the staging sibling at the destination without ever replacing an existing file.
        The sibling stays (it proves ownership until commit). Raises FileExistsError when the
        destination exists (a competing writer)."""
        try:
            if os.environ.get(FAULT_VARIABLE) == 'no_hard_links':     # tests: the fallback path
                raise OSError(errno.EPERM, 'injected: no hard links')
            os.link(item.staging, item.destination)
        except FileExistsError:
            raise
        except OSError as error:
            # No hard links here (some network or FAT volumes): exclusive create, claim, then fill.
            if error.errno not in {errno.EPERM, errno.ENOTSUP, getattr(errno, 'EOPNOTSUPP', errno.ENOTSUP), errno.EMLINK, errno.ENOSYS}:
                raise
            # CRT text mode on Windows would translate LF bytes inside images.
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
            fd = os.open(str(item.destination), flags, 0o644)
            try:
                failpoint('after_exclusive_create')
                item.claimed = True
                _write_json(self._coordinator, self._journal_record('PREPARED'))   # durable: this file is ours
                failpoint('after_exclusive_claim')
                with open(item.staging, 'rb') as inp:
                    for index, chunk in enumerate(iter(lambda: inp.read(COPY_CHUNK), b'')):
                        os.write(fd, chunk)
                        if index == 0:
                            failpoint('during_exclusive_fill')
                os.fsync(fd)
            finally:
                os.close(fd)
            return 'exclusive-create'
        return 'hard-link'

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
                item.absent, item.original, item.backup, item.claimed = True, None, None, False
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
                    _fault('stage' if item is self.items[-1] else '')
                    _copy_flushed(item.source, item.staging)
                failpoint('after_staging')
                for item in self.items:
                    if item.backup is not None:
                        _fault('backup')
                        os.replace(item.destination, item.backup)
                        moved.append(item)
                        failpoint('after_first_backup') if len(moved) == 1 else None
                failpoint('after_backups')
                for item in self.items:
                    _fault('publish' if item is self.items[-1] else '')
                    try:
                        item.method = self._publish(item)
                    except FileExistsError:
                        raise PublicationConflict(
                            f'{item.destination.name} was created by another writer while this export ran; '
                            f'nothing was replaced (remove or rename that file, or export elsewhere)')
                    item.published_sha256 = item.staged_sha256
                    published.append(item)
                    if len(published) == 1:
                        failpoint('after_first_publish')
                failpoint('before_commit_decision')
                # The durable decision: the coordinator's COMMITTED record, before any backup goes.
                _write_json(self._coordinator, self._journal_record('COMMITTED'))
            except BaseException as failure:
                problems = self._rollback(published, moved)
                if problems:
                    raise RecoveryRequired(
                        f'{failure}; restoring the previous export also failed for {"; ".join(problems)} — the journal '
                        f'{JOURNAL} and the backups stay in {folders[0]} for recovery') from failure
                self._remove_journals()
                raise
            failpoint('after_commit_decision')
            # Committed: what follows is housekeeping. A failure here never rolls anything back.
            pending = []
            for folder in folders[1:]:
                try:
                    _fault('participant_journal')
                    _write_json(Path(folder) / JOURNAL, self._journal_record('COMMITTED'))
                except OSError as error:
                    pending.append(f'{Path(folder) / JOURNAL}: {error}')
            for index, item in enumerate(self.items):
                if item.backup is not None and item.backup.exists():
                    try:
                        _fault('cleanup' if index == 1 else '')
                        item.backup.unlink()
                    except OSError as error:
                        pending.append(f'{item.backup.name}: {error}')
                    if index == 0:
                        failpoint('after_first_backup_removed')
                if item.staging is not None and item.staging.exists():
                    try:
                        item.staging.unlink()
                    except OSError as error:
                        pending.append(f'{item.staging.name}: {error}')
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
        """Undo a pre-commit failure: remove what this request published (proven by its sibling or
        claim, never by bytes alone), put backups back where the destination is free. Returns
        the problems it could not fix."""
        problems = []
        for item in published:
            try:
                if item.destination.exists():
                    if _owned(item.destination, item.staging, item.claimed, item.staged_sha256):
                        item.destination.unlink()
                    else:
                        problems.append(f'{item.destination.name} was changed by another writer since this export published it')
            except OSError as error:
                problems.append(f'{item.destination.name}: {error}')
        for item in moved:
            try:
                if item.backup.exists():
                    if item.destination.exists():
                        problems.append(f'{item.destination.name} is occupied by another writer; its previous version stays in {item.backup.name}')
                    else:
                        os.replace(item.backup, item.destination)
            except OSError as error:
                problems.append(f'{item.destination.name} (backup {item.backup.name}): {error}')
        for item in self.items:
            if item.staging is not None and item.staging.exists():
                try:
                    item.staging.unlink()
                except OSError:
                    pass
        return problems


# ---- recovery -----------------------------------------------------------------------------------

def _owned(destination, staging, claimed, staged_sha256):
    """Whether the file at ``destination`` is provably this transaction's, untouched by anyone
    else: its staging sibling is the same file (hard-link publication) and it still holds the
    recorded staged bytes, or its exclusive creation was recorded and it holds a prefix of the
    staged bytes (a complete or interrupted fill). A published file another writer edited in
    place (which edits the sibling too) is not ours to delete."""
    if claimed:
        if staging is not None and staging.exists():
            return _is_prefix(destination, staging)
        return _sha256(destination) == staged_sha256
    if staging is not None and staging.exists() and _same_file(staging, destination):
        return _sha256(destination) == staged_sha256
    return False


def _is_prefix(path, whole):
    """Whether ``path`` holds a (possibly complete) prefix of ``whole``."""
    with open(path, 'rb') as part, open(whole, 'rb') as full:
        while True:
            chunk = part.read(COPY_CHUNK)
            if not chunk:
                return True
            if full.read(len(chunk)) != chunk:
                return False


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
    if not isinstance(data, dict) or data.get('format') not in ACCEPTED_FORMATS:
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
        original = item.get('original')
        if original is not None and not (isinstance(original, dict) and isinstance(original.get('sha256'), str)):
            raise RecoveryRequired(f'the export journal {journal} has an invalid original record for {name}; kept for recovery')
        if not item.get('absent') and original is None:
            raise RecoveryRequired(f'the export journal {journal} records an original of {name} without its digest; kept for recovery')
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


def _finish_committed(item, destination, staging, backup, actions):
    """Post-commit housekeeping for one item: the new output stays, backup and sibling go."""
    problems = []
    for leftover, label in ((backup, 'backup'), (staging, 'staging')):
        if leftover is not None and leftover.exists():
            try:
                leftover.unlink()
                actions.append(f'removed {label} {leftover.name}')
            except OSError as error:
                problems.append(f'{leftover.name}: {error}')
    if not destination.exists():
        actions.append(f'note: committed output {destination.name} is missing (removed by another writer)')
    return problems


def _undo_item(item, destination, staging, backup, original, actions):
    """Return one pre-commit item to its pre-request state. Returns a problem description
    when the safe course is to keep what is there, else None. Order: decide the destination
    from evidence, restore, and only then remove the sibling (the proof of ownership)."""
    name = destination.name
    absent = bool(item.get('absent'))
    original_sha = (original or {}).get('sha256')
    staged_sha = item.get('staged_sha256') or ''
    backup_exists = backup is not None and backup.exists()
    if destination.exists():
        if _owned(destination, staging, item.get('claimed'), staged_sha):
            if not absent and not backup_exists:
                # Our file stands where the original was and the backup is gone: the destination is
                # the only copy of whatever it holds. Equal to the original, it is the original's
                # content and stays; otherwise the original is lost and nothing may be deleted.
                if _sha256(destination) == original_sha:
                    actions.append(f'kept {name} (unchanged content; its backup was already gone)')
                    _remove_sibling(staging, actions)
                    return None
                return (f'the previous version of {name} is missing (no backup) and the destination holds this '
                        f'export\'s file; both are kept as they are')
            destination.unlink()
            actions.append(f'removed unpublished {name}')
        else:
            current = _sha256(destination)
            if not absent and current == original_sha:
                # The original, untouched or already restored — even when the replacement had the
                # same bytes. A surviving backup is a duplicate of a verified copy.
                if backup_exists:
                    backup.unlink()
                    actions.append(f'removed duplicate backup {backup.name}')
                actions.append(f'kept {name} (the original)')
                _remove_sibling(staging, actions)
                return None
            tail = ('' if absent else (f'; the previous version stays in {backup.name}' if backup_exists
                                       else ' and its previous version has no backup'))
            if current == staged_sha:
                return (f'{name} has this export\'s bytes but cannot be attributed to this export (another writer '
                        f'may have created it, or an older journal recorded no proof); it is kept{tail}')
            if destination.stat().st_size == 0:
                return (f'{name} is empty: another writer\'s file, or a creation of this export interrupted before '
                        f'it was recorded; it is kept{tail}')
            return f'{name} holds another writer\'s file; it is kept{tail}'
    if not destination.exists():
        if backup_exists:
            os.replace(backup, destination)
            actions.append(f'restored {name}')
        elif not absent:
            return f'{name} existed before this export and is now missing with no backup to restore; nothing was deleted'
    _remove_sibling(staging, actions)
    return None


def _remove_sibling(staging, actions):
    if staging is not None and staging.exists():
        staging.unlink()
        actions.append(f'removed staging {staging.name}')


def _apply_decision(decision, folders):
    identifier = decision['transaction']
    committed = decision['state'] == 'COMMITTED'
    actions, conflicts = [], []
    for item in decision['items']:
        folder = Path(item['folder']).resolve()
        destination = folder / item['name']
        staging = folder / item['staging'] if item.get('staging') else None
        backup = folder / item['backup'] if item.get('backup') else None
        changed_before = len(actions)
        if committed:
            conflicts.extend(_finish_committed(item, destination, staging, backup, actions))
        else:
            try:
                problem = _undo_item(item, destination, staging, backup, item.get('original'), actions)
            except OSError as error:
                problem = f'{destination.name}: {error}'
            if problem:
                conflicts.append(problem)
        if len(actions) > changed_before:
            failpoint('recover_after_first_change')
    if conflicts:
        raise RecoveryRequired(f'recovery of export {identifier} needs attention: ' + '; '.join(conflicts)
                               + f'; the journal(s) and backups are kept in {", ".join(str(f) for f in folders)}')
    failpoint('recover_before_journal_removal')
    # Complete: participants' journals first, the coordinator (the decision) last.
    coordinator = Path(decision['coordinator']).resolve()
    try:
        for folder in sorted(folders, key=lambda f: f == coordinator.parent):
            journal = folder / JOURNAL
            if journal.exists() and journal.resolve() != coordinator:
                _fault('recover_journal_removal')
                journal.unlink()
                failpoint('recover_after_participant_journals')
        if coordinator.exists():
            _fault('recover_journal_removal')
            coordinator.unlink()
    except OSError as error:
        raise RecoveryRequired(f'recovery of export {identifier} restored the files but could not remove its journal '
                               f'({error}); run it again') from error
    actions.append('committed outputs kept' if committed else 'previous export restored')
    return actions
