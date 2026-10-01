"""S1 stability reproduction: the two identical-bytes recovery sequences through the real
transaction path (Transaction.commit in a worker, public recover here). Writes JSON evidence.

    python scripts/stability_recovery_repro.py <repo-root>
"""
import hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path
root = Path(sys.argv[1]); sys.path.insert(0, str(root / 'src'))
from open_painter.export import transaction
WORKER = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from open_painter.export import transaction
spec = json.loads(Path(sys.argv[2]).read_text())
tx = transaction.Transaction(identifier=spec["identifier"])
for d, s in spec["items"]: tx.add(d, s)
print(tx.commit(overwrite=True).message)
'''
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def snap(f): return {p.name: sha(p) for p in sorted(Path(f).iterdir()) if p.is_file()}
results = {}
same = b'identical BaseColor bytes\n' * 100
# Case 1: identical original, interrupted after PREPARED before any backup.
tmp = tempfile.mkdtemp(prefix='op-s0-'); out = Path(tmp, 'out'); st = Path(tmp, 'staging'); out.mkdir(); st.mkdir()
(out / 'BaseColor.png').write_bytes(same); (st / 'BaseColor.png').write_bytes(same)
spec = Path(tmp, 'spec.json'); spec.write_text(json.dumps({'identifier': 'feedfacecafe', 'items': [(str(out / 'BaseColor.png'), str(st / 'BaseColor.png'))]}), encoding="utf-8")
env = dict(os.environ, OP_EXPORT_FAILPOINT='after_prepared')
r = subprocess.run([sys.executable, '-c', WORKER, str(root / 'src'), str(spec)], env=env, capture_output=True, text=True)
before = snap(out)
try:
    actions = transaction.recover(out); err = None
except Exception as e:
    actions = []; err = repr(e)
results['case1_after_prepared_identical'] = {'worker_exit': r.returncode, 'before_recovery': before, 'actions': actions, 'error': err,
    'after_recovery': snap(out), 'original_preserved': (out / 'BaseColor.png').exists() and sha(out / 'BaseColor.png') == hashlib.sha256(same).hexdigest()}
# Case 2: original moved to backup, first recovery restores it, journal cleanup interrupted, recovery runs again.
tmp = tempfile.mkdtemp(prefix='op-s0-'); out = Path(tmp, 'out'); st = Path(tmp, 'staging'); out.mkdir(); st.mkdir()
(out / 'BaseColor.png').write_bytes(same); (st / 'BaseColor.png').write_bytes(same)
spec = Path(tmp, 'spec.json'); spec.write_text(json.dumps({'identifier': 'feedfacecafe', 'items': [(str(out / 'BaseColor.png'), str(st / 'BaseColor.png'))]}), encoding="utf-8")
env = dict(os.environ, OP_EXPORT_FAILPOINT='after_backups')
r = subprocess.run([sys.executable, '-c', WORKER, str(root / 'src'), str(spec)], env=env, capture_output=True, text=True)
before = snap(out)
real_unlink = Path.unlink
def failing_unlink(self, missing_ok=False):
    if self.name == transaction.JOURNAL: raise OSError('injected: journal cleanup interrupted')
    return real_unlink(self, missing_ok=missing_ok)
Path.unlink = failing_unlink
try:
    try: first = transaction.recover(out); first_err = None
    except Exception as e: first = []; first_err = repr(e)
finally: Path.unlink = real_unlink
mid = snap(out)
try: second = transaction.recover(out); second_err = None
except Exception as e: second = []; second_err = repr(e)
results['case2_restored_then_journal_cleanup_failed'] = {'worker_exit': r.returncode, 'before_first_recovery': before, 'first': first, 'first_error': first_err,
    'after_first_recovery': mid, 'second': second, 'second_error': second_err, 'after_second_recovery': snap(out),
    'original_preserved': (out / 'BaseColor.png').exists() and sha(out / 'BaseColor.png') == hashlib.sha256(same).hexdigest()}
print(json.dumps(results, indent=1))

# Write the evidence file named by the module's FORMAT, so before/after are distinguishable.
out_dir = root / '.blender-test/quality/stability'
out_dir.mkdir(parents=True, exist_ok=True)
name = 's1-recovery-after.json' if transaction.FORMAT >= 3 else 's0-recovery-before.json'
(out_dir / name).write_text(json.dumps(results, indent=1), encoding="utf-8")
print('WROTE', out_dir / name)
