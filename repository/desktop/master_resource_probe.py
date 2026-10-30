"""Read-only Windows resource counters for one worker's evidenced process tree.

No process is started, stopped, injected, or opened for write. The probe reads
only PIDs, parent PIDs, executable basenames, creation/CPU times and working-set
counters. It does not read command lines, environments, windows, or file data.
Detached processes are not inferred from a matching executable name.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import os


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
        ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
        ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260),
    ]


class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
        ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
        ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
        ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t),
        ('PrivateUsage', ctypes.c_size_t),
    ]


def _ticks(value):
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _unavailable(reason, worker_pid):
    return {
        'format': 'interlinked-master-resource-sample-v1',
        'observedAt': datetime.now(timezone.utc).isoformat(),
        'workerPid': worker_pid, 'status': 'unavailable', 'processes': [],
        'totals': {'cpuMs': None, 'workingSetBytes': None},
        'coverage': 'ToolHelp parent-PID descendants only; detached processes are not inferred',
        'errors': [reason],
    }


def _api():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ('Process32FirstW', 'Process32NextW'):
        fn = getattr(kernel, name)
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        fn.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    try:
        memory = kernel.K32GetProcessMemoryInfo
    except AttributeError:
        memory = ctypes.WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
    memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESS_MEMORY_COUNTERS_EX), wintypes.DWORD]
    memory.restype = wintypes.BOOL
    return kernel, memory


def _rows(kernel, snapshot):
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    ctypes.set_last_error(0)
    more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
    while more:
        yield entry
        more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    error = ctypes.get_last_error()
    if error not in (0, 18):  # ERROR_NO_MORE_FILES is the normal end of a snapshot.
        raise OSError(error, 'Process metadata enumeration unavailable')


def _read_process(kernel, memory, pid, parent_pid, name, worker_pid):
    row = {
        'pid': pid, 'parentPid': parent_pid, 'name': name,
        'relation': 'worker' if pid == worker_pid else 'descendant',
        'createdAtTicks': None, 'cpuMs': None, 'workingSetBytes': None,
        'status': 'unavailable', 'unavailable': {},
    }
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        reason = 'OpenProcess denied or process exited (winerror %d)' % ctypes.get_last_error()
        row['unavailable'] = {'cpuMs': reason, 'workingSetBytes': reason, 'createdAtTicks': reason}
        return row
    try:
        created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
        if kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                  ctypes.byref(system), ctypes.byref(user)):
            row['createdAtTicks'] = _ticks(created)
            row['cpuMs'] = (_ticks(system) + _ticks(user)) / 10000.0
        else:
            reason = 'GetProcessTimes unavailable (winerror %d)' % ctypes.get_last_error()
            row['unavailable'].update(cpuMs=reason, createdAtTicks=reason)
        counters = _PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(counters)
        if memory(handle, ctypes.byref(counters), counters.cb):
            row['workingSetBytes'] = int(counters.WorkingSetSize)
        else:
            row['unavailable']['workingSetBytes'] = 'GetProcessMemoryInfo unavailable (winerror %d)' % ctypes.get_last_error()
        code = wintypes.DWORD()
        if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259:
            row['cpuMs'] = row['workingSetBytes'] = None
            row['unavailable'].update(cpuMs='Process exited during sample',
                                      workingSetBytes='Process exited during sample')
        present = sum(row[field] is not None for field in ('cpuMs', 'workingSetBytes'))
        row['status'] = 'available' if present == 2 else 'partial' if present else 'unavailable'
        return row
    finally:
        kernel.CloseHandle(handle)


def sample_owned(worker_pid):
    """Return cumulative CPU milliseconds and current working sets, never fake zeroes.

    Identity for rate calculations is (pid, createdAtTicks); CPU totals can fall
    when a process exits, so callers should compute deltas per stable identity.
    Elevation can make working-set counters unavailable. Parent-PID enumeration
    cannot prove ownership of a process detached by an elevation service; such a
    process is omitted rather than guessed from its name.
    """
    if isinstance(worker_pid, bool) or not isinstance(worker_pid, int) or not 0 < worker_pid <= 0xffffffff:
        raise ValueError('worker_pid must be a positive Windows PID')
    if os.name != 'nt':
        return _unavailable('Windows process metadata APIs are unavailable on this platform', worker_pid)
    try:
        kernel, memory = _api()
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            return _unavailable('Process snapshot unavailable (winerror %d)' % ctypes.get_last_error(), worker_pid)
        try:
            # Only numeric relationships are retained for unrelated processes.
            parents = {int(row.th32ProcessID): int(row.th32ParentProcessID) for row in _rows(kernel, snapshot)}
            if worker_pid not in parents:
                return _unavailable('Requested worker is absent from the process snapshot', worker_pid)
            selected = {worker_pid}
            while True:
                descendants = {pid for pid, parent in parents.items() if parent in selected}
                expanded = selected | descendants
                if len(expanded) > 4096:
                    return _unavailable('Worker process tree exceeds the bounded sampling limit', worker_pid)
                if expanded == selected:
                    break
                selected = expanded
            # Basenames are materialized only for the selected worker tree.
            names = {int(row.th32ProcessID): str(row.szExeFile) for row in _rows(kernel, snapshot)
                     if int(row.th32ProcessID) in selected}
        finally:
            kernel.CloseHandle(snapshot)
        rows = [_read_process(kernel, memory, pid, parents[pid], names.get(pid, ''), worker_pid)
                for pid in sorted(selected, key=lambda value: (value != worker_pid, value))]
        by_pid = {row['pid']: row for row in rows}
        errors = []
        # A reused parent PID must not turn an older unrelated process into a child.
        rejected = set()
        for row in rows:
            if row['pid'] == worker_pid:
                continue
            parent = by_pid.get(row['parentPid'])
            if parent and parent['createdAtTicks'] is not None and row['createdAtTicks'] is not None:
                if row['createdAtTicks'] < parent['createdAtTicks']:
                    rejected.add(row['pid'])
        while True:
            expanded = rejected | {row['pid'] for row in rows if row['parentPid'] in rejected}
            if expanded == rejected:
                break
            rejected = expanded
        if rejected:
            rows = [row for row in rows if row['pid'] not in rejected]
            errors.append('Excluded process-parent relationships with a reused parent PID')
        if rows and all(row['status'] == 'available' for row in rows) and not errors:
            status = 'available'
        elif any(row[field] is not None for row in rows for field in ('cpuMs', 'workingSetBytes')):
            status = 'partial'
        else:
            status = 'unavailable'
        totals = {field: sum(row[field] for row in rows) if rows and all(row[field] is not None for row in rows) else None
                  for field in ('cpuMs', 'workingSetBytes')}
        return {
            'format': 'interlinked-master-resource-sample-v1',
            'observedAt': datetime.now(timezone.utc).isoformat(),
            'workerPid': worker_pid, 'status': status, 'processes': rows,
            'totals': totals,
            'coverage': 'ToolHelp parent-PID descendants only; detached processes are not inferred',
            'errors': errors,
        }
    except (OSError, AttributeError) as exc:
        return _unavailable('Windows metadata sampling unavailable: ' + type(exc).__name__, worker_pid)
