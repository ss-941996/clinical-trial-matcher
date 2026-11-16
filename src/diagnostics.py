"""Record non-sensitive local hardware and runtime facts for reproducibility."""
import ctypes
import json
import os
import platform
import shutil
import sqlite3
import sys
from importlib.metadata import version
from src.common import ROOT,now,write_json

def main():
    memory=None
    if os.name=='nt':
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_=[('dwLength',ctypes.c_ulong),('dwMemoryLoad',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in ['ullTotalPhys','ullAvailPhys','ullTotalPageFile','ullAvailPageFile','ullTotalVirtual','ullAvailVirtual','ullAvailExtendedVirtual']]
        m=MEMORYSTATUSEX(); m.dwLength=ctypes.sizeof(m)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            memory={'total_bytes':m.ullTotalPhys,'available_bytes':m.ullAvailPhys}
    disk=shutil.disk_usage(ROOT)
    facts=dict(created_at=now(),python=sys.version,platform=platform.platform(),logical_cpus=os.cpu_count(),memory=memory,
               disk={'total_bytes':disk.total,'free_bytes':disk.free},sqlite=sqlite3.sqlite_version,
               packages={p:version(p) for p in ['pyarrow','numpy','requests','pytest','pytrec-eval-terrier']})
    write_json(ROOT/'reports/environment.json',facts)
    print(json.dumps(facts,indent=2))
if __name__=='__main__': main()
