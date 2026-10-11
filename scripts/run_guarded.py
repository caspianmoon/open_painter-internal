"""Run a command (usually Blender) under a hard memory cap. macOS and Windows.

A large Blender benchmark once took a development machine down (2026-09-30), so
heavy runs should go through this guard. It:

- refuses to start while available memory is below ``--start-gb`` (waiting up to
  ``--wait-seconds``);
- takes an exclusive lock, so two guarded runs never overlap;
- polls the memory footprint of the process tree every 0.2 s. When the tree passes
  the cap, it kills the tree;
- checks available system memory every second. When it falls below
  ``--floor-gb``, it also kills the tree, whatever its own size: other
  applications may be growing too;
- prints the peak footprint and the first time each whole gigabyte was reached.

What the footprint is depends on the platform:

- macOS: the physical footprint of each process (Activity Monitor's Memory column).
  On Apple silicon that includes GPU allocations; RSS does not. Available memory is
  free, inactive, speculative and purgeable pages. The tree is killed with SIGKILL.
- Windows: the command starts suspended and joins a Job Object before it runs, so every
  process it starts belongs to the job. The footprint is the job's commit charge
  (private bytes, backed by RAM or the page file), and the kernel refuses the job any
  commit past the cap plus 1 GiB: a backstop for growth faster than the poll. A discrete
  GPU's dedicated memory is not in the commit charge, unlike the Mac footprint: the
  summary reports its peak (``gpu_dedicated_peak_gib``) but does not cap it. Available
  memory is GlobalMemoryStatusEx's ullAvailPhys (Task Manager's Available). The job is
  terminated, and is killed too when the guard exits (KILL_ON_JOB_CLOSE).

Usage::

    python3 scripts/run_guarded.py --cap-gb 6 -- /Applications/Blender.app/Contents/MacOS/Blender ...
    python scripts/run_guarded.py --cap-gb 6 -- "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe" ...

The command's exit code is returned. A killed run exits with 137 and prints
GUARD_KILLED.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
GIB = 1024 ** 3
WINDOWS = sys.platform == 'win32'

if WINDOWS:
    from ctypes import wintypes
    import msvcrt
else:
    import fcntl
    _libproc = ctypes.CDLL('/usr/lib/libproc.dylib')
    _libproc.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    _libproc.proc_pid_rusage.restype = ctypes.c_int
    _libproc.proc_listchildpids.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    _libproc.proc_listchildpids.restype = ctypes.c_int
RUSAGE_INFO_V2 = 2
FOOTPRINT_OFFSET = 16 + 7 * 8   # ri_uuid, then ri_phys_footprint is the eighth uint64


def footprint(pid):
    """Physical footprint in bytes (Activity Monitor's Memory column), 0 when gone. macOS."""
    buffer = (ctypes.c_uint8 * 256)()
    if _libproc.proc_pid_rusage(pid, RUSAGE_INFO_V2, buffer) != 0:
        return 0
    return int.from_bytes(bytes(buffer[FOOTPRINT_OFFSET:FOOTPRINT_OFFSET + 8]), 'little')


def children(pid):
    """A process's direct children, from the kernel (libproc). macOS."""
    size = 64
    while True:
        buffer = (ctypes.c_int * size)()
        count = _libproc.proc_listchildpids(pid, buffer, ctypes.sizeof(buffer))
        if count < 0:
            return []
        if count < size:
            return [buffer[i] for i in range(count) if buffer[i] > 0]
        size *= 4   # a full buffer may have cut the list short


def tree(pid):
    """The process and its descendants. macOS: under a millisecond, so every poll walks it."""
    found, work = [], [pid]
    while work:
        current = work.pop()
        found.append(current)
        work.extend(children(current))
    return found


def available():
    """Bytes the system can hand out without swapping much."""
    if WINDOWS:
        status = _MemoryStatus(dwLength=ctypes.sizeof(_MemoryStatus))
        if not _kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise ctypes.WinError(ctypes.get_last_error())
        return status.ullAvailPhys
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


def lock(path):
    """The machine-wide guard lock, held until the returned file closes; None when taken."""
    handle = open(path, 'a+', encoding="utf-8")
    try:
        if WINDOWS:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


class _Tree:
    """macOS: the command and its descendants, found with ps and killed with SIGKILL."""

    def __init__(self, command):
        self.process = subprocess.Popen(command, start_new_session=True)
        self.members = [self.process.pid]

    def used(self):
        # Every poll: a descendant missing from the sum could allocate unseen until the next walk.
        self.members = tree(self.process.pid)
        return sum(footprint(pid) for pid in self.members)

    def sample(self):
        pass

    def kill(self):
        for pid in reversed(tree(self.process.pid)):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def peak(self):
        return 0

    def summary(self):
        return {}


if WINDOWS:
    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _ntdll = ctypes.WinDLL('ntdll')
    CREATE_SUSPENDED = 0x4
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JOB_BASIC_PROCESS_ID_LIST, JOB_EXTENDED_LIMIT_INFORMATION = 3, 9
    PROCESS_VM_READ, PROCESS_QUERY_LIMITED_INFORMATION = 0x10, 0x1000
    ERROR_MORE_DATA = 234
    PDH_FMT_LARGE, PDH_MORE_DATA = 0x400, 0x800007D2

    class _MemoryStatus(ctypes.Structure):   # MEMORYSTATUSEX
        _fields_ = [('dwLength', wintypes.DWORD), ('dwMemoryLoad', wintypes.DWORD),
                    *((name, ctypes.c_uint64) for name in (
                        'ullTotalPhys', 'ullAvailPhys', 'ullTotalPageFile', 'ullAvailPageFile',
                        'ullTotalVirtual', 'ullAvailVirtual', 'ullAvailExtendedVirtual'))]

    class _BasicLimits(ctypes.Structure):   # JOBOBJECT_BASIC_LIMIT_INFORMATION
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                    ('SchedulingClass', wintypes.DWORD)]

    class _Limits(ctypes.Structure):   # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        _fields_ = [('BasicLimitInformation', _BasicLimits), ('IoInfo', ctypes.c_uint64 * 6),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

    class _ProcessIds(ctypes.Structure):   # JOBOBJECT_BASIC_PROCESS_ID_LIST, room for 256
        _fields_ = [('NumberOfAssignedProcesses', wintypes.DWORD), ('NumberOfProcessIdsInList', wintypes.DWORD),
                    ('ProcessIdList', ctypes.c_size_t * 256)]

    class _MemoryCounters(ctypes.Structure):   # PROCESS_MEMORY_COUNTERS_EX
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                    *((name, ctypes.c_size_t) for name in (
                        'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage',
                        'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage', 'PagefileUsage',
                        'PeakPagefileUsage', 'PrivateUsage'))]

    class _PdhValue(ctypes.Structure):   # PDH_FMT_COUNTERVALUE read as largeValue
        _fields_ = [('CStatus', wintypes.DWORD), ('largeValue', ctypes.c_int64)]

    class _PdhItem(ctypes.Structure):   # PDH_FMT_COUNTERVALUE_ITEM_W
        _fields_ = [('szName', ctypes.c_wchar_p), ('FmtValue', _PdhValue)]

    for _name, _result, _arguments in (
            ('CreateJobObjectW', wintypes.HANDLE, [ctypes.c_void_p, wintypes.LPCWSTR]),
            ('SetInformationJobObject', wintypes.BOOL, [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]),
            ('QueryInformationJobObject', wintypes.BOOL,
             [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]),
            ('AssignProcessToJobObject', wintypes.BOOL, [wintypes.HANDLE, wintypes.HANDLE]),
            ('TerminateJobObject', wintypes.BOOL, [wintypes.HANDLE, wintypes.UINT]),
            ('OpenProcess', wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
            ('K32GetProcessMemoryInfo', wintypes.BOOL, [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]),
            ('CloseHandle', wintypes.BOOL, [wintypes.HANDLE]),
            ('GlobalMemoryStatusEx', wintypes.BOOL, [ctypes.c_void_p])):
        getattr(_kernel32, _name).restype = _result
        getattr(_kernel32, _name).argtypes = _arguments
    _ntdll.NtResumeProcess.restype = ctypes.c_long
    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]

    def _check(succeeded):
        if not succeeded:
            raise ctypes.WinError(ctypes.get_last_error())


class _GpuMemory:
    """Windows: GPU memory per process, from the WDDM "GPU Process Memory" performance counters."""
    PATHS = {'dedicated': r'\GPU Process Memory(*)\Dedicated Usage',
             'shared': r'\GPU Process Memory(*)\Shared Usage'}

    def __init__(self):
        self.pdh = ctypes.WinDLL('pdh')
        for name, arguments in (
                ('PdhOpenQueryW', [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.c_void_p]),
                ('PdhAddEnglishCounterW', [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_size_t, ctypes.c_void_p]),
                ('PdhCollectQueryData', [ctypes.c_void_p]),
                ('PdhGetFormattedCounterArrayW',
                 [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p])):
            getattr(self.pdh, name).restype = ctypes.c_uint32
            getattr(self.pdh, name).argtypes = arguments
        self.query = ctypes.c_void_p()
        if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)):
            raise OSError('PdhOpenQueryW failed')
        self.counters = {}
        for name, path in self.PATHS.items():
            counter = ctypes.c_void_p()
            if self.pdh.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(counter)):
                raise OSError(f'No performance counter {path}')
            self.counters[name] = counter

    def usage(self, pids):
        """Bytes of each kind that these processes hold, or None when the counters fail."""
        if self.pdh.PdhCollectQueryData(self.query):
            return None
        usage = {}
        for name, counter in self.counters.items():
            size, count = wintypes.DWORD(0), wintypes.DWORD(0)
            status = self.pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_LARGE, ctypes.byref(size),
                                                           ctypes.byref(count), None)
            usage[name] = 0
            if status != PDH_MORE_DATA:
                continue   # no instances: no process uses the GPU
            buffer = (ctypes.c_byte * size.value)()
            if self.pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_LARGE, ctypes.byref(size),
                                                     ctypes.byref(count), buffer):
                return None
            for item in ctypes.cast(buffer, ctypes.POINTER(_PdhItem * count.value)).contents:
                match = re.match(r'pid_(\d+)_', item.szName or '')
                if match and int(match.group(1)) in pids and item.FmtValue.CStatus in (0, 1):
                    usage[name] += item.FmtValue.largeValue
        return usage


class _Job:
    """Windows: the command in a Job Object, which every process it starts joins."""

    def __init__(self, command, backstop):
        self.handle = _kernel32.CreateJobObjectW(None, None)
        _check(self.handle)
        limits = _Limits()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_JOB_MEMORY
        limits.JobMemoryLimit = backstop
        _check(_kernel32.SetInformationJobObject(self.handle, JOB_EXTENDED_LIMIT_INFORMATION,
                                                 ctypes.byref(limits), ctypes.sizeof(limits)))
        # Suspended until it is in the job, so nothing it starts can escape the job.
        self.process = subprocess.Popen(command, creationflags=CREATE_SUSPENDED)
        try:
            _check(_kernel32.AssignProcessToJobObject(self.handle, int(self.process._handle)))
            status = _ntdll.NtResumeProcess(int(self.process._handle))
            if status:
                raise OSError(f'NtResumeProcess failed with status {status & 0xFFFFFFFF:#x}')
        except OSError:
            self.process.kill()
            raise
        try:
            self.gpu = _GpuMemory()
        except OSError:
            self.gpu = None
        self.gpu_peak = {'dedicated': 0, 'shared': 0} if self.gpu else None
        self.processes = 1   # most processes in the job at once (Blender's shader compilers join it)
        self.resident = 0   # largest sampled working set of the job: physical memory in use

    def pids(self):
        ids = _ProcessIds()
        if not _kernel32.QueryInformationJobObject(self.handle, JOB_BASIC_PROCESS_ID_LIST, ctypes.byref(ids),
                                                   ctypes.sizeof(ids), None):
            if ctypes.get_last_error() != ERROR_MORE_DATA:   # more than 256: the first 256 count
                return [self.process.pid]
        return list(ids.ProcessIdList[:ids.NumberOfProcessIdsInList])

    def used(self):
        """Commit charge (private bytes) of every process in the job."""
        total, resident, pids = 0, 0, self.pids()
        self.processes = max(self.processes, len(pids))
        for pid in pids:
            process = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
            if not process:
                continue   # exited since the listing
            try:
                counters = _MemoryCounters(cb=ctypes.sizeof(_MemoryCounters))
                if _kernel32.K32GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
                    total += counters.PrivateUsage
                    resident += counters.WorkingSetSize
            finally:
                _kernel32.CloseHandle(process)
        self.resident = max(self.resident, resident)
        return total

    def sample(self):
        """Once a second: the GPU memory the job's processes hold."""
        if not self.gpu:
            return
        usage = self.gpu.usage(set(self.pids()))
        if usage is None:
            return
        for name, value in usage.items():
            self.gpu_peak[name] = max(self.gpu_peak[name], value)

    def kill(self):
        _kernel32.TerminateJobObject(self.handle, 137)

    def peak(self):
        """The kernel's peak commit of the job: exact, where the poll samples."""
        limits = _Limits()
        if not _kernel32.QueryInformationJobObject(self.handle, JOB_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits),
                                                   ctypes.sizeof(limits), None):
            return 0
        return limits.PeakJobMemoryUsed

    def summary(self):
        gpu = self.gpu_peak or {}
        return dict(measure='job commit charge; dedicated GPU memory is not included and not capped',
                    working_set_peak_gib=round(self.resident / GIB, 2), processes_peak=self.processes,
                    gpu_dedicated_peak_gib=round(gpu['dedicated'] / GIB, 2) if gpu else None,
                    gpu_shared_peak_gib=round(gpu['shared'] / GIB, 2) if gpu else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--cap-gb', type=float, default=6.0, help='Kill the run above this footprint')
    parser.add_argument('--start-gb', type=float, default=3.0, help='Available memory required to start')
    parser.add_argument('--floor-gb', type=float, default=1.0,
                        help='Kill the run when available system memory falls below this')
    parser.add_argument('--reserve-gb', type=float, default=None, help=argparse.SUPPRESS)  # accepted, unused
    parser.add_argument('--wait-seconds', type=float, default=0.0,
                        help='Wait this long for enough available memory before refusing')
    parser.add_argument('--timeout-seconds', type=float, default=0.0,
                        help='Kill the run when it is still running after this long (0: no limit); '
                             'a hung process fails instead of stalling everything queued behind it')
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
    held = lock(lock_path)
    if held is None:
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
    run = _Job(command, cap + GIB) if WINDOWS else _Tree(command)
    process = run.process
    started, peak, marks, killed, reason = time.monotonic(), 0, {}, False, ''
    polls, lowest = 0, free
    while process.poll() is None:
        used = run.used()
        polls += 1
        peak = max(peak, used)
        mark = used // GIB
        if mark and mark not in marks:
            marks[int(mark)] = round(time.monotonic() - started, 1)
        if polls % 5 == 0:
            system = available()
            lowest = min(lowest, system)
            run.sample()
            if system < args.floor_gb * GIB:
                reason = f'available system memory fell to {system / GIB:.1f} GiB'
        if args.timeout_seconds and time.monotonic() - started > args.timeout_seconds:
            reason = f'still running after {args.timeout_seconds:g} s'
        if used > cap:
            reason = f'footprint passed {args.cap_gb} GiB'
        if reason:
            run.kill()
            killed = True
            process.wait()
            break
        time.sleep(0.2)
    peak = max(peak, run.peak())
    code = 137 if killed else process.returncode
    summary = dict(peak_gib=round(peak / GIB, 2), cap_gib=args.cap_gb, killed=killed, exit=code,
                   seconds=round(time.monotonic() - started, 1), gib_reached_at_s=marks,
                   available_gib_at_start=round(free / GIB, 1), lowest_available_gib=round(lowest / GIB, 1),
                   reason=reason, **run.summary())
    if killed:
        print(f'GUARD_KILLED {reason}', flush=True)
    print('GUARD_SUMMARY', json.dumps(summary), flush=True)
    if args.report:
        args.report.write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return code


if __name__ == '__main__':
    sys.exit(main())
