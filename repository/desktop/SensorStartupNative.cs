// Fixed contained vendor launch primitives shared with the reviewed MASTER worker.
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
internal static partial class SensorStartup {
        [StructLayout(LayoutKind.Sequential)]private struct JOB_BASIC {internal long ProcessTime,JobTime;internal uint LimitFlags;internal UIntPtr MinWorkingSet,MaxWorkingSet;internal uint ActiveProcesses;internal UIntPtr Affinity;internal uint PriorityClass,SchedulingClass;}
        [StructLayout(LayoutKind.Sequential)]private struct IO_COUNTERS {internal ulong ReadOperations,WriteOperations,OtherOperations,ReadBytes,WriteBytes,OtherBytes;}
        [StructLayout(LayoutKind.Sequential)]private struct JOB_EXTENDED {internal JOB_BASIC Basic;internal IO_COUNTERS Io;internal UIntPtr ProcessMemory,JobMemory,PeakProcessMemory,PeakJobMemory;}
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern IntPtr CreateJobObject(IntPtr security,string name);
        [DllImport("kernel32.dll",SetLastError=true)]private static extern bool SetInformationJobObject(IntPtr job,int info,IntPtr data,uint length);
        [DllImport("kernel32.dll",SetLastError=true)]private static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
        [DllImport("kernel32.dll")]private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll",SetLastError=true)]private static extern bool GetNamedPipeServerProcessId(IntPtr pipe,out uint pid);
        [DllImport("kernel32.dll",SetLastError=true)]private static extern bool GetNamedPipeClientProcessId(IntPtr pipe,out uint pid);
        [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]private struct STARTUPINFO {internal int Size;internal string Reserved,Desktop,Title;internal int X,Y,Width,Height,CharsX,CharsY,Fill,Flags;internal short ShowWindow,ReservedSize;internal IntPtr ReservedPointer,StdInput,StdOutput,StdError;}
        [StructLayout(LayoutKind.Sequential)]private struct PROCESS_INFORMATION {internal IntPtr Process,Thread;internal uint ProcessId,ThreadId;}
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]private static extern bool CreateProcess(string application,StringBuilder command,IntPtr processSecurity,IntPtr threadSecurity,bool inherit,uint flags,IntPtr environment,string directory,ref STARTUPINFO startup,out PROCESS_INFORMATION information);
        [DllImport("kernel32.dll",SetLastError=true)]private static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll",SetLastError=true)]private static extern bool TerminateProcess(IntPtr process,uint code);
        private static IntPtr NewHelperJob()
        {
            IntPtr job=CreateJobObject(IntPtr.Zero,null);if(job==IntPtr.Zero)throw new IOException("Cannot create helper containment.");
            var limits=new JOB_EXTENDED();limits.Basic.LimitFlags=0x2000;int size=Marshal.SizeOf(typeof(JOB_EXTENDED));IntPtr memory=Marshal.AllocHGlobal(size);
            try{Marshal.StructureToPtr(limits,memory,false);if(!SetInformationJobObject(job,9,memory,(uint)size))throw new IOException("Cannot set helper containment.");return job;}catch{CloseHandle(job);throw;}finally{Marshal.FreeHGlobal(memory);}
        }
        private static Process StartContainedSensor(IntPtr job)
        {
            var startup=new STARTUPINFO();startup.Size=Marshal.SizeOf(typeof(STARTUPINFO));startup.Flags=1;startup.ShowWindow=0;PROCESS_INFORMATION child;
            if(!CreateProcess(@"C:\Program Files\TRCCCAP\HWINFO.exe",null,IntPtr.Zero,IntPtr.Zero,false,0x08000004,IntPtr.Zero,@"C:\Program Files\TRCCCAP",ref startup,out child))throw new IOException("Reviewed sensor helper could not start.");
            try{if(!AssignProcessToJobObject(job,child.Process))throw new IOException("Cannot contain sensor helper.");if(ResumeThread(child.Thread)==uint.MaxValue)throw new IOException("Cannot resume contained sensor helper.");return Process.GetProcessById((int)child.ProcessId);}catch{TerminateProcess(child.Process,1);throw;}finally{CloseHandle(child.Thread);CloseHandle(child.Process);}
        }
}
