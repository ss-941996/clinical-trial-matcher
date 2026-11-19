"""Launch the offline hybrid demo from any working directory, on one CPU."""
import ctypes
import os
from pathlib import Path
import sys

root=Path(__file__).resolve().parent.parent
os.chdir(root)
sys.dont_write_bytecode=True
for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:
    os.environ[name]='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
os.environ['TRIAL_PIPELINE']='hybrid'
if os.name=='nt':
    kernel=ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype=ctypes.c_void_p
    process=kernel.GetCurrentProcess()
    kernel.GetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_size_t)]
    kernel.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    kernel.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
    mask=ctypes.c_size_t();system=ctypes.c_size_t()
    if not kernel.GetProcessAffinityMask(process,ctypes.byref(mask),ctypes.byref(system)):raise ctypes.WinError()
    if not kernel.SetProcessAffinityMask(process,1<<(mask.value.bit_length()-1)):raise ctypes.WinError()
    if not kernel.SetPriorityClass(process,0x4000):raise ctypes.WinError()
elif hasattr(os,'sched_getaffinity'):
    os.sched_setaffinity(0,{max(os.sched_getaffinity(0))})
    os.nice(10)
deps=os.environ.get('TRIAL_DEMO_DEPS')
if deps:sys.path.insert(0,str(Path(deps).resolve()))
import uvicorn
if __name__=='__main__':
    uvicorn.run('app.main:app',host=os.environ.get('HOST','127.0.0.1'),port=int(os.environ.get('PORT','8000')),workers=1,access_log=False)
