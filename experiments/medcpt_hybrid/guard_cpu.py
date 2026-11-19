"""Windows CPU guard for explicitly identified local pilot workers only.

Default: pin both workers to the same single logical CPU without pausing them.
Optional --auto-pause adds high-load suspension. All observations and actions
are persisted. No model imports.
"""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parents[2]
from experiments.medcpt_hybrid.pilot import OUT


class Thresholds:
    def __init__(self):
        self.high=0;self.low=0

    def update(self,cpu,paused):
        self.high=self.high+1 if cpu>=75 else 0
        self.low=self.low+1 if cpu<=60 else 0
        if not paused and self.high>=3:
            self.high=0;self.low=0;return 'pause'
        if paused and self.low>=2:
            self.high=0;self.low=0;return 'resume'
        return None


class Windows:
    def __init__(self):
        self.k=ctypes.WinDLL('kernel32',use_last_error=True)
        self.n=ctypes.WinDLL('ntdll')
        self.k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        self.k.OpenProcess.restype=wintypes.HANDLE
        self.k.CloseHandle.argtypes=[wintypes.HANDLE]
        self.k.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        self.k.GetProcessAffinityMask.argtypes=[wintypes.HANDLE,ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_size_t)]
        self.k.SetProcessAffinityMask.argtypes=[wintypes.HANDLE,ctypes.c_size_t]
        self.k.SetPriorityClass.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        self.n.NtQueryInformationProcess.argtypes=[wintypes.HANDLE,wintypes.ULONG,ctypes.c_void_p,wintypes.ULONG,ctypes.POINTER(wintypes.ULONG)]
        self.n.NtSuspendProcess.argtypes=[wintypes.HANDLE]
        self.n.NtResumeProcess.argtypes=[wintypes.HANDLE]

    def command(self,handle):
        size=wintypes.ULONG()
        self.n.NtQueryInformationProcess(handle,60,None,0,ctypes.byref(size))
        if not size.value:raise RuntimeError('Cannot inspect target process')
        buf=ctypes.create_string_buffer(size.value)
        if self.n.NtQueryInformationProcess(handle,60,buf,size.value,ctypes.byref(size))!=0:
            raise RuntimeError('Cannot inspect target command')
        class UnicodeString(ctypes.Structure):
            _fields_=[('Length',wintypes.USHORT),('MaximumLength',wintypes.USHORT),('Buffer',ctypes.c_void_p)]
        text=UnicodeString.from_buffer(buf)
        return ctypes.wstring_at(text.Buffer,text.Length//2)

    def open_worker(self,pid):
        handle=self.k.OpenProcess(0x400|0x200|0x800,False,pid)
        if not handle:raise ctypes.WinError(ctypes.get_last_error())
        try:
            command=self.command(handle)
            if str(ROOT).lower() not in command.lower() or not any(
                text in command for text in ['-m experiments.medcpt_hybrid.pilot --stage ',
                                            '-m experiments.medcpt_hybrid.finish_pilot']):
                raise ValueError(f'Refusing unrelated PID {pid}')
            return dict(pid=pid,handle=handle,command=command,paused=False)
        except BaseException:
            self.k.CloseHandle(handle);raise

    def alive(self,worker):
        code=wintypes.DWORD()
        return bool(self.k.GetExitCodeProcess(worker['handle'],ctypes.byref(code))) and code.value==259

    def cpu_times(self):
        idle=wintypes.FILETIME();kernel=wintypes.FILETIME();user=wintypes.FILETIME()
        if not self.k.GetSystemTimes(ctypes.byref(idle),ctypes.byref(kernel),ctypes.byref(user)):
            raise ctypes.WinError(ctypes.get_last_error())
        convert=lambda v:(v.dwHighDateTime<<32)|v.dwLowDateTime
        return convert(idle),convert(kernel)+convert(user)

    def worker_cpu_seconds(self,worker):
        created=wintypes.FILETIME();exited=wintypes.FILETIME()
        kernel=wintypes.FILETIME();user=wintypes.FILETIME()
        self.k.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
        if not self.k.GetProcessTimes(worker['handle'],ctypes.byref(created),ctypes.byref(exited),ctypes.byref(kernel),ctypes.byref(user)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (((kernel.dwHighDateTime<<32)|kernel.dwLowDateTime)+
                ((user.dwHighDateTime<<32)|user.dwLowDateTime))/10_000_000

    def suspend(self,worker):
        if self.alive(worker) and not worker['paused']:
            if self.n.NtSuspendProcess(worker['handle'])!=0:raise RuntimeError('Failed to pause worker')
            worker['paused']=True

    def resume(self,worker):
        if worker['paused']:
            if self.alive(worker) and self.n.NtResumeProcess(worker['handle'])!=0:
                raise RuntimeError('Failed to resume worker')
            worker['paused']=False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pids',nargs='+',type=int,required=True)
    parser.add_argument('--auto-pause',action='store_true',help='Opt in to 75/60 percent whole-machine CPU hysteresis; disabled by default')
    parser.add_argument('--resume-previous',action='store_true',help='Recover these same workers if a prior guard recorded them paused')
    args=parser.parse_args()
    if os.name!='nt':raise RuntimeError('This guard targets Windows pilot processes')
    OUT.mkdir(parents=True,exist_ok=True)
    api=Windows();workers=[];thresholds=Thresholds()
    def log(kind,**data):
        record=dict(observed_at_utc=datetime.now(timezone.utc).isoformat(timespec='seconds'),kind=kind,**data)
        line=json.dumps(record)
        with (OUT/'guardrail.log').open('a',encoding='utf-8') as stream:stream.write(line+'\n')
        human=f"[{record['observed_at_utc']}] cpu_guard/{kind}: {json.dumps(data)}\n"
        with (OUT/'live.log').open('a',encoding='utf-8') as stream:stream.write(human)
        print(line,flush=True)
    try:
        workers=[api.open_worker(pid) for pid in args.pids]
        previous_state=OUT/'guardrail-state.json'
        if args.resume_previous and previous_state.exists():
            old=json.loads(previous_state.read_text())
            if old.get('paused') and set(old.get('pids',[]))==set(args.pids):
                for worker in workers:
                    worker['paused']=True;api.resume(worker)
                log('workers_resumed',reason='User selected continuous execution with a fixed CPU cap')
        available=[]
        for worker in workers:
            mask=ctypes.c_size_t();system=ctypes.c_size_t()
            if not api.k.GetProcessAffinityMask(worker['handle'],ctypes.byref(mask),ctypes.byref(system)):
                raise ctypes.WinError(ctypes.get_last_error())
            available.append(mask.value)
        common=available[0]
        for mask in available[1:]:common&=mask
        if not common:raise RuntimeError('No common allowed logical CPU')
        chosen=1<<(common.bit_length()-1)
        for worker in workers:
            if not api.k.SetProcessAffinityMask(worker['handle'],chosen):raise ctypes.WinError(ctypes.get_last_error())
            if not api.k.SetPriorityClass(worker['handle'],0x4000):raise ctypes.WinError(ctypes.get_last_error())
        log('guard_enabled',pids=args.pids,affinity_mask=chosen,logical_cpu=chosen.bit_length()-1,
            logical_cpu_count=os.cpu_count(),approx_max_total_cpu_percent=100/os.cpu_count(),
            mode='auto_pause' if args.auto_pause else 'fixed_single_cpu',automatic_pausing=args.auto_pause)
        previous=api.cpu_times()
        previous_worker_cpu={w['pid']:api.worker_cpu_seconds(w) for w in workers}
        previous_wall=time.monotonic();last_heartbeat=0.
        while any(api.alive(worker) for worker in workers):
            time.sleep(5);current=api.cpu_times()
            wall=time.monotonic();interval=wall-previous_wall;previous_wall=wall
            live_workers=[];cpu_delta=0.
            for worker in workers:
                alive=api.alive(worker)
                seconds=api.worker_cpu_seconds(worker)
                delta=max(0.,seconds-previous_worker_cpu[worker['pid']])
                previous_worker_cpu[worker['pid']]=seconds;cpu_delta+=delta
                live_workers.append(dict(pid=worker['pid'],alive=alive,cpu_seconds=seconds,cpu_delta_seconds=delta))
            idle=current[0]-previous[0];total=current[1]-previous[1];previous=current
            cpu=100*(1-idle/total) if total else 0.
            paused=any(w['paused'] for w in workers)
            action=thresholds.update(cpu,paused) if args.auto_pause else None
            if action=='pause':
                for worker in workers:api.suspend(worker)
                log('workers_paused',system_cpu_percent=cpu,reason='Three consecutive samples >=75%')
            elif action=='resume':
                for worker in workers:api.resume(worker)
                log('workers_resumed',system_cpu_percent=cpu,reason='Two consecutive samples <=60%')
            state=dict(observed_at_utc=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                system_cpu_percent=cpu,paused=any(w['paused'] for w in workers),affinity_mask=chosen,
                high_samples=thresholds.high,low_samples=thresholds.low,pids=args.pids,
                mode='auto_pause' if args.auto_pause else 'fixed_single_cpu',automatic_pausing=args.auto_pause,
                workers=live_workers,experiment_cpu_percent_of_machine=100*cpu_delta/interval/os.cpu_count())
            progress_path=OUT/'encoding-progress.json'
            if progress_path.exists():
                progress=json.loads(progress_path.read_text())
                state.update(encoded=progress['completed'],encoding_total=progress['total'],
                    checkpoint_age_seconds=max(0.,time.time()-progress_path.stat().st_mtime))
            with (OUT/'guardrail-samples.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(state)+'\n')
            temp=OUT/'guardrail-state.tmp';temp.write_text(json.dumps(state,indent=2)+'\n')
            temp.replace(OUT/'guardrail-state.json')
            if wall-last_heartbeat>=30:
                log('heartbeat',**{key:state[key] for key in ['system_cpu_percent',
                    'experiment_cpu_percent_of_machine','workers','paused','mode']},
                    encoded=state.get('encoded'),checkpoint_age_seconds=state.get('checkpoint_age_seconds'))
                last_heartbeat=wall
        log('workers_finished')
    finally:
        for worker in workers:
            try:api.resume(worker)
            finally:api.k.CloseHandle(worker['handle'])


if __name__=='__main__':main()
