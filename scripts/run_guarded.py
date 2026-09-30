"""Run a command (usually Blender) under a hard memory cap. macOS only.

A large Blender benchmark once took a development machine down (2026-09-30), so
heavy runs should go through this guard. It:

- refuses to start while available memory (free, inactive, speculative and
  purgeable pages) is below ``--start-gb`` (waiting up to ``--wait-seconds``);
- takes an exclusive lock, so two guarded runs never overlap;
- polls the physical footprint of the process tree every 0.2 s. On Apple
  silicon that includes GPU allocations; RSS does not. When the tree passes
  the cap, it kills the tree with SIGKILL;
- checks available system memory every second. When it falls below
  ``--floor-gb``, it also kills the tree, whatever its own size: other
  applications may be growing too;
- prints the peak footprint and the first time each whole gigabyte was reached.

Usage::

    python3 scripts/run_guarded.py --cap-gb 6 -- /Applications/Blender.app/Contents/MacOS/Blender ...

The command's exit code is returned. A killed run exits with 137 and prints
GUARD_KILLED.
"""
import argparse
import ctypes
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
GIB = 1024 ** 3
_libproc = ctypes.CDLL('/usr/lib/libproc.dylib')
_libproc.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
_libproc.proc_pid_rusage.restype = ctypes.c_int
RUSAGE_INFO_V2 = 2
FOOTPRINT_OFFSET = 16 + 7 * 8   # ri_uuid, then ri_phys_footprint is the eighth uint64


def footprint(pid):
    """Physical footprint in bytes (Activity Monitor's Memory column), 0 when gone."""
    buffer = (ctypes.c_uint8 * 256)()
    if _libproc.proc_pid_rusage(pid, RUSAGE_INFO_V2, buffer) != 0:
        return 0
    return int.from_bytes(bytes(buffer[FOOTPRINT_OFFSET:FOOTPRINT_OFFSET + 8]), 'little')


def tree(pid):
    """The process and its descendants."""
    try:
        rows = subprocess.run(['ps', '-A', '-o', 'pid=,ppid='], capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError:
        return [pid]
    children = {}
    for line in rows.split('\n'):
        parts = line.split()
        if len(parts) == 2:
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, work = [], [pid]
    while work:
        current = work.pop()
        found.append(current)
        work.extend(children.get(current, ()))
    return found


def available():
    """Bytes the system can hand out without swapping much (vm_stat page classes)."""
    text = subprocess.run(['vm_stat'], capture_output=True, text=True, check=True).stdout
    size = int(text.split('page size of ')[1].split(' ')[0])
    pages = {}
    for line in text.split('\n')[1:]:
        if ':' in line:
            name, value = line.split(':', 1)
            try:
                pages[name.strip()] = int(value.strip().rstrip('.'))
            except ValueError:
                pass
    usable = sum(pages.get(name, 0) for name in ('Pages free', 'Pages inactive', 'Pages speculative', 'Pages purgeable'))
    return usable * size


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--cap-gb', type=float, default=6.0, help='Kill the run above this footprint')
    parser.add_argument('--start-gb', type=float, default=3.0, help='Available memory required to start')
    parser.add_argument('--floor-gb', type=float, default=1.0,
                        help='Kill the run when available system memory falls below this')
    parser.add_argument('--reserve-gb', type=float, default=None, help=argparse.SUPPRESS)  # accepted, unused
    parser.add_argument('--wait-seconds', type=float, default=0.0,
                        help='Wait this long for enough available memory before refusing')
    parser.add_argument('--report', type=Path, help='Write the peak and timeline as JSON')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('No command given')
    cap = int(args.cap_gb * GIB)
    # One lock for the machine: worktrees and other checkouts must not overlap either.
    lock_path = Path(os.environ.get('OP_GUARD_LOCK') or Path(tempfile.gettempdir()) / 'open_painter_guard.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = open(lock_path, 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('GUARD_REFUSED another guarded run is active', flush=True)
        return 75
    free = available()
    deadline = time.monotonic() + args.wait_seconds
    while free < args.start_gb * GIB and time.monotonic() < deadline:
        time.sleep(5)
        free = available()
    if free < args.start_gb * GIB:
        print(f'GUARD_REFUSED only {free / GIB:.1f} GiB available; need {args.start_gb:.1f}', flush=True)
        return 75
    process = subprocess.Popen(command, start_new_session=True)
    started, peak, marks, killed, reason = time.monotonic(), 0, {}, False, ''
    members = [process.pid]
    polls, lowest = 0, free
    while process.poll() is None:
        if polls % 10 == 0:
            members = tree(process.pid)   # children are rare; refresh every two seconds
        polls += 1
        used = sum(footprint(pid) for pid in members)
        peak = max(peak, used)
        mark = used // GIB
        if mark and mark not in marks:
            marks[int(mark)] = round(time.monotonic() - started, 1)
        if polls % 5 == 0:
            system = available()
            lowest = min(lowest, system)
            if system < args.floor_gb * GIB:
                reason = f'available system memory fell to {system / GIB:.1f} GiB'
        if used > cap:
            reason = f'footprint passed {args.cap_gb} GiB'
        if reason:
            for pid in reversed(tree(process.pid)):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            killed = True
            process.wait()
            break
        time.sleep(0.2)
    code = 137 if killed else process.returncode
    summary = dict(peak_gib=round(peak / GIB, 2), cap_gib=args.cap_gb, killed=killed, exit=code,
                   seconds=round(time.monotonic() - started, 1), gib_reached_at_s=marks,
                   available_gib_at_start=round(free / GIB, 1), lowest_available_gib=round(lowest / GIB, 1),
                   reason=reason)
    if killed:
        print(f'GUARD_KILLED {reason}', flush=True)
    print('GUARD_SUMMARY', json.dumps(summary), flush=True)
    if args.report:
        args.report.write_text(json.dumps(summary, indent=2) + '\n')
    return code


if __name__ == '__main__':
    sys.exit(main())
