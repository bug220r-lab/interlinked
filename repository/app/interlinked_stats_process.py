"""Windows 10+ Stats process-tree guard, adapted from reviewed msi_job_process.py.

The optional stdin is a private parent-owned control pipe, never an HTTP path.

The caller must hold its workspace lock for this entire call. This is lifecycle
containment, not a security sandbox: code must not launch work through external
brokers, WMI, services, or separately registered tasks. Ordinary CreateProcess
descendants stay in the non-breakaway job and must finish before return.

Microsoft references:
https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute
https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw
"""
from __future__ import annotations
from contextlib import nullcontext
import ctypes
from ctypes import wintypes as w
import msvcrt
import os
from pathlib import Path
import subprocess
import time

SIZE=ctypes.c_size_t
HANDLE=ctypes.c_void_p
DWORD=ctypes.c_uint32
BOOL=ctypes.c_int
class STARTUPINFO(ctypes.Structure):
    _fields_=[('cb',DWORD),('lpReserved',w.LPWSTR),('lpDesktop',w.LPWSTR),('lpTitle',w.LPWSTR),('dwX',DWORD),('dwY',DWORD),('dwXSize',DWORD),('dwYSize',DWORD),('dwXCountChars',DWORD),('dwYCountChars',DWORD),('dwFillAttribute',DWORD),('dwFlags',DWORD),('wShowWindow',ctypes.c_ushort),('cbReserved2',ctypes.c_ushort),('lpReserved2',ctypes.c_void_p),('hStdInput',HANDLE),('hStdOutput',HANDLE),('hStdError',HANDLE)]
class STARTUPINFOEX(ctypes.Structure):
    _fields_=[('StartupInfo',STARTUPINFO),('lpAttributeList',ctypes.c_void_p)]
class PROCESS_INFORMATION(ctypes.Structure):
    _fields_=[('hProcess',HANDLE),('hThread',HANDLE),('dwProcessId',DWORD),('dwThreadId',DWORD)]
class BASIC_LIMIT(ctypes.Structure):
    _fields_=[('PerProcessUserTimeLimit',ctypes.c_int64),('PerJobUserTimeLimit',ctypes.c_int64),('LimitFlags',DWORD),('MinimumWorkingSetSize',SIZE),('MaximumWorkingSetSize',SIZE),('ActiveProcessLimit',DWORD),('Affinity',SIZE),('PriorityClass',DWORD),('SchedulingClass',DWORD)]
class IO_COUNTERS(ctypes.Structure):
    _fields_=[(name,ctypes.c_uint64) for name in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
class EXTENDED_LIMIT(ctypes.Structure):
    _fields_=[('BasicLimitInformation',BASIC_LIMIT),('IoInfo',IO_COUNTERS),('ProcessMemoryLimit',SIZE),('JobMemoryLimit',SIZE),('PeakProcessMemoryUsed',SIZE),('PeakJobMemoryUsed',SIZE)]
class ACCOUNTING(ctypes.Structure):
    _fields_=[('TotalUserTime',ctypes.c_int64),('TotalKernelTime',ctypes.c_int64),('ThisPeriodTotalUserTime',ctypes.c_int64),('ThisPeriodTotalKernelTime',ctypes.c_int64),('TotalPageFaultCount',DWORD),('TotalProcesses',DWORD),('ActiveProcesses',DWORD),('TotalTerminatedProcesses',DWORD)]

def _api():
    if os.name!='nt':raise RuntimeError('Windows process containment is required')
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    signatures={
        'CreateJobObjectW':([ctypes.c_void_p,w.LPCWSTR],HANDLE),
        'SetInformationJobObject':([HANDLE,ctypes.c_int,ctypes.c_void_p,DWORD],BOOL),
        'QueryInformationJobObject':([HANDLE,ctypes.c_int,ctypes.c_void_p,DWORD,ctypes.POINTER(DWORD)],BOOL),
        'TerminateJobObject':([HANDLE,ctypes.c_uint],BOOL),
        'IsProcessInJob':([HANDLE,HANDLE,ctypes.POINTER(BOOL)],BOOL),
        'GetCurrentProcess':([],HANDLE),
        'DuplicateHandle':([HANDLE,HANDLE,HANDLE,ctypes.POINTER(HANDLE),DWORD,BOOL,DWORD],BOOL),
        'CloseHandle':([HANDLE],BOOL),
        'InitializeProcThreadAttributeList':([ctypes.c_void_p,DWORD,DWORD,ctypes.POINTER(SIZE)],BOOL),
        'UpdateProcThreadAttribute':([ctypes.c_void_p,DWORD,SIZE,ctypes.c_void_p,SIZE,ctypes.c_void_p,ctypes.c_void_p],BOOL),
        'DeleteProcThreadAttributeList':([ctypes.c_void_p],None),
        'CreateProcessW':([w.LPCWSTR,w.LPWSTR,ctypes.c_void_p,ctypes.c_void_p,BOOL,DWORD,ctypes.c_void_p,w.LPCWSTR,ctypes.POINTER(STARTUPINFOEX),ctypes.POINTER(PROCESS_INFORMATION)],BOOL),
        'ResumeThread':([HANDLE],DWORD),
        'WaitForSingleObject':([HANDLE,DWORD],DWORD),
        'GetExitCodeProcess':([HANDLE,ctypes.POINTER(DWORD)],BOOL),
    }
    for name,(args,result) in signatures.items():
        function=getattr(k,name);function.argtypes=args;function.restype=result
    return k

def _checked(value):
    if not value:raise ctypes.WinError(ctypes.get_last_error())
    return value

def run_process_tree(argv,*,cwd,env,stdout,stderr,stdin=None,on_started=None,on_poll=None):
    """Run a contained process tree; callback(pid) runs before user code resumes.

    stdout/stderr are already-open binary files owned by the caller. on_poll,
    when supplied, receives the active process count; it is useful for tests or
    a caller's cancellation signal. Exceptions terminate the entire job before
    returning control to the caller. No task/network/global settings are changed.
    """
    if not isinstance(argv,(list,tuple)) or not argv or not all(isinstance(x,str) and '\0' not in x for x in argv):raise ValueError('Expected a nonempty argv string list without NUL')
    if not Path(argv[0]).is_absolute() or Path(argv[0]).suffix.lower()!='.exe':raise ValueError('Executable must be an absolute .exe path')
    if not Path(cwd).is_absolute():raise ValueError('Working directory must be absolute')
    command=subprocess.list2cmdline(argv)
    if len(command)>=32767:raise ValueError('Windows command line is too long')
    if not isinstance(env,dict) or not all(isinstance(a,str) and a and '=' not in a and '\0' not in a and isinstance(b,str) and '\0' not in b for a,b in env.items()):raise ValueError('Invalid environment mapping')
    environment=ctypes.create_unicode_buffer('\0'.join(a+'='+b for a,b in sorted(env.items(),key=lambda x:x[0].upper()))+'\0\0')
    command_buffer=ctypes.create_unicode_buffer(command)
    k=_api();job=None;attribute_memory=None;attribute_ready=False;duplicates=[];process=PROCESS_INFORMATION();success=False
    with (nullcontext(stdin) if stdin is not None else open(os.devnull,'rb',buffering=0)) as null_input:
        try:
            job=_checked(k.CreateJobObjectW(None,None))
            limits=EXTENDED_LIMIT();limits.BasicLimitInformation.LimitFlags=0x00002000  # KILL_ON_JOB_CLOSE; neither breakaway flag.
            _checked(k.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)))
            current=k.GetCurrentProcess()
            for stream in (null_input,stdout,stderr):
                original=msvcrt.get_osfhandle(stream.fileno());duplicate=HANDLE()
                _checked(k.DuplicateHandle(current,original,current,ctypes.byref(duplicate),0,True,2))
                duplicates.append(duplicate.value)
            handles=(HANDLE*3)(*duplicates);jobs=(HANDLE*1)(job)
            needed=SIZE();k.InitializeProcThreadAttributeList(None,2,0,ctypes.byref(needed))
            if not needed.value:raise ctypes.WinError(ctypes.get_last_error())
            attribute_memory=ctypes.create_string_buffer(needed.value)
            _checked(k.InitializeProcThreadAttributeList(attribute_memory,2,0,ctypes.byref(needed)));attribute_ready=True
            _checked(k.UpdateProcThreadAttribute(attribute_memory,0,0x00020002,handles,ctypes.sizeof(handles),None,None))
            # Atomic job assignment closes the create-then-assign crash window.
            # The Windows10+ job-list attribute is mandatory; no unsafe fallback.
            _checked(k.UpdateProcThreadAttribute(attribute_memory,0,0x0002000D,jobs,ctypes.sizeof(jobs),None,None))
            startup=STARTUPINFOEX();startup.StartupInfo.cb=ctypes.sizeof(startup);startup.lpAttributeList=ctypes.cast(attribute_memory,ctypes.c_void_p)
            startup.StartupInfo.dwFlags=0x00000100|0x00000001;startup.StartupInfo.wShowWindow=0
            startup.StartupInfo.hStdInput=duplicates[0];startup.StartupInfo.hStdOutput=duplicates[1];startup.StartupInfo.hStdError=duplicates[2]
            # A private hidden console lets launch.py deliver CTRL_BREAK to its
            # Streamlit process group for graceful SQLite/application shutdown.
            # SW_HIDE above prevents an interactive console window appearing.
            flags=0x00000004|0x00000400|0x00080000|0x00000010  # suspended, Unicode environment, extended startup, new console
            _checked(k.CreateProcessW(argv[0],command_buffer,None,None,True,flags,environment,str(cwd),ctypes.byref(startup),ctypes.byref(process)))
            contained=BOOL();_checked(k.IsProcessInJob(process.hProcess,job,ctypes.byref(contained)))
            if not contained.value:raise RuntimeError('Created process was not assigned to the owned job')
            if on_started is not None:on_started(int(process.dwProcessId))
            if k.ResumeThread(process.hThread)==0xFFFFFFFF:raise ctypes.WinError(ctypes.get_last_error())
            k.CloseHandle(process.hThread);process.hThread=None
            # Parent copies must not keep log handles live after the job ends.
            for handle in duplicates:k.CloseHandle(handle)
            duplicates=[]
            direct_exit=None;direct_exit_at=None
            while True:
                if process.hProcess:
                    waited=k.WaitForSingleObject(process.hProcess,100)
                    if waited==0:
                        code=DWORD();_checked(k.GetExitCodeProcess(process.hProcess,ctypes.byref(code)));direct_exit=int(code.value)
                        direct_exit_at=time.monotonic()
                        k.CloseHandle(process.hProcess);process.hProcess=None
                    elif waited!=258:raise ctypes.WinError(ctypes.get_last_error())
                else:time.sleep(0.1)
                info=ACCOUNTING();_checked(k.QueryInformationJobObject(job,1,ctypes.byref(info),ctypes.sizeof(info),None))
                if on_poll is not None:on_poll(int(info.ActiveProcesses))
                if info.ActiveProcesses and direct_exit_at is not None and (direct_exit != 0 or time.monotonic()-direct_exit_at > 5):
                    raise RuntimeError('Stats launcher exited with live descendants; contained recovery is required')
                if info.ActiveProcesses==0:
                    if direct_exit is None:raise RuntimeError('Process tree ended without a recorded direct exit code')
                    success=True;return direct_exit
        finally:
            if job and not success:
                # The job was assigned atomically before any user code ran.
                # A hard runner termination closes this sole, non-inherited
                # job handle in the kernel and invokes the same kill policy.
                # Never return to the caller (and release its cache lock) merely
                # because cleanup timed out or accounting temporarily failed.
                # A suspended process can leave accounting before its process
                # handle signals, so both conditions are required. If Windows
                # cannot confirm termination, remain here with the lock held.
                while True:
                    try:
                        k.TerminateJobObject(job,0xC000013A)
                        direct_done=not process.hProcess or k.WaitForSingleObject(process.hProcess,100)==0
                        if direct_done and process.hProcess:
                            k.CloseHandle(process.hProcess);process.hProcess=None
                        info=ACCOUNTING()
                        known=k.QueryInformationJobObject(job,1,ctypes.byref(info),ctypes.sizeof(info),None)
                        if direct_done and known and info.ActiveProcesses==0:break
                        time.sleep(0.05)
                    except KeyboardInterrupt:
                        # A second Ctrl+C must not bypass the lifetime guard.
                        continue
            if process.hThread:k.CloseHandle(process.hThread)
            if process.hProcess:k.CloseHandle(process.hProcess)
            if attribute_ready:k.DeleteProcThreadAttributeList(attribute_memory)
            for handle in duplicates:k.CloseHandle(handle)
            if job:k.CloseHandle(job)
