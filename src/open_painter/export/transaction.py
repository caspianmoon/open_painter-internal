"""Transactional publication of an export request (add-on quality remedy, M2B).

Every file of a request (maps of every set, manifests, the mesh) is generated into
staging first. Publication then runs as one transaction on the destination filesystem:

1. The destinations are validated (no duplicate paths across sets; with ``overwrite`` off,
   none may exist: files are created exclusively).
2. A journal is written next to the outputs listing every destination, its backup name and
   its staging name, so an interrupted publication can be recovered.
3. Existing destinations are renamed to backups (a rename on the same filesystem, so the
   original bytes stay intact until the end).
4. Each staged file is copied to a staging name beside its destination, flushed to disk,
   then renamed into place (atomic on POSIX).
5. On any failure the destinations written so far are removed and every backup renamed
   back; the journal is removed last. On success the backups and the journal go.

``recover`` runs before a publication (and can be called on load): a journal left behind
by a crash names what to put back, so a destination folder is restored to the state
before the interrupted request without guessing from file names.
"""
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

JOURNAL = '.open_painter_export_journal.json'


@dataclass
class Item:
    destination: Path
    source: Path            # the staged file
    backup: Path = None     # where the previous destination went, if there was one
    staging: Path = None    # the temporary name beside the destination

    def record(self):
        return {'destination': str(self.destination), 'backup': str(self.backup) if self.backup else None,
                'staging': str(self.staging) if self.staging else None}


@dataclass
class Transaction:
    items: list = field(default_factory=list)
    identifier: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def add(self, destination, source):
        self.items.append(Item(Path(destination), Path(source)))

    @property
    def destinations(self):
        return [item.destination for item in self.items]

    def validate(self, overwrite):
        seen = {}
        for item in self.items:
            key = str(item.destination.resolve())
            if key in seen:
                raise ValueError(f'Two outputs would write the same file: {item.destination.name}')
            seen[key] = item
            if not item.source.exists():
                raise FileNotFoundError(f'Staged file missing: {item.source}')
        if not overwrite:
            existing = [item.destination.name for item in self.items if item.destination.exists()]
            if existing:
                raise ValueError('Export files already exist: ' + ', '.join(existing[:5])
                                 + (' …' if len(existing) > 5 else '') + '. Choose another folder, prefix or suffix, or allow overwriting.')

    def journals(self):
        """One journal per destination folder, so recovery finds it beside the outputs."""
        folders = {}
        for item in self.items:
            folders.setdefault(item.destination.parent, []).append(item)
        return folders

    def commit(self, overwrite=False):
        """Publish every item or none; returns the destinations."""
        self.validate(overwrite)
        for folder in self.journals():
            recover(folder)         # a crashed earlier request is put back before this one starts
        for item in self.items:
            item.staging = item.destination.with_name(f'{item.destination.name}.op-staging-{self.identifier}')
            if item.destination.exists():
                item.backup = item.destination.with_name(f'{item.destination.name}.op-backup-{self.identifier}')
        written, journal_paths, moved = [], [], []
        try:
            for folder, items in self.journals().items():
                folder.mkdir(parents=True, exist_ok=True)
                journal = folder / JOURNAL
                journal.write_text(json.dumps({'transaction': self.identifier, 'items': [i.record() for i in items]}, indent=1))
                journal_paths.append(journal)
            for item in self.items:
                if item.backup is not None:
                    os.replace(item.destination, item.backup)
                    moved.append(item)
            for item in self.items:
                _copy_flushed(item.source, item.staging)
                os.replace(item.staging, item.destination)
                written.append(item)
        except BaseException as failure:
            # Roll back what this request did; a restore that fails itself keeps its backup and
            # the journal in place and names them, so nothing recoverable is cleaned away.
            stuck = []
            for item in written:
                try:
                    item.destination.unlink(missing_ok=True)
                except OSError as error:
                    stuck.append(f'{item.destination.name}: {error}')
            for item in self.items:
                if item.staging is not None:
                    try:
                        item.staging.unlink(missing_ok=True)
                    except OSError:
                        pass
            for item in moved:
                try:
                    if item.backup.exists():
                        os.replace(item.backup, item.destination)
                except OSError as error:
                    stuck.append(f'{item.destination.name} (backup {item.backup.name}): {error}')
            if stuck:
                raise RuntimeError(f'{failure}; restoring the previous export also failed for '
                                   f'{"; ".join(stuck)} — the backups and {JOURNAL} stay in '
                                   f'{self.items[0].destination.parent} for recovery') from failure
            for journal in journal_paths:
                journal.unlink(missing_ok=True)
            raise
        for item in self.items:
            if item.backup is not None:
                item.backup.unlink(missing_ok=True)
        for journal in journal_paths:
            journal.unlink(missing_ok=True)
        return self.destinations


def _copy_flushed(source, target):
    with open(source, 'rb') as inp, open(target, 'wb') as out:
        shutil.copyfileobj(inp, out, 1 << 20)
        out.flush()
        os.fsync(out.fileno())


def recover(folder):
    """Undo an interrupted publication in ``folder`` from its journal: every destination whose
    backup still exists gets the backup back, partial staging files go, the journal goes.
    Returns what was restored (destination names) or an empty list."""
    folder = Path(folder)
    journal = folder / JOURNAL
    if not journal.exists():
        return []
    try:
        data = json.loads(journal.read_text())
    except (ValueError, OSError):
        data = {'items': []}
    restored = []
    for record in data.get('items', []):
        destination = Path(record['destination'])
        staging = Path(record['staging']) if record.get('staging') else None
        backup = Path(record['backup']) if record.get('backup') else None
        if staging is not None and staging.exists():
            staging.unlink(missing_ok=True)
        if backup is not None and backup.exists():
            os.replace(backup, destination)
            restored.append(destination.name)
        elif backup is None and destination.exists():
            # A destination created by the interrupted request (nothing to put back): it is
            # complete only if the request committed, which it did not, so it goes.
            destination.unlink(missing_ok=True)
            restored.append(f'-{destination.name}')
    journal.unlink(missing_ok=True)
    return restored
