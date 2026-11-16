import os
from pathlib import Path
import sys
import time
sys.dont_write_bytecode=True
deps=os.environ.get('TRIAL_DEMO_DEPS')
if deps:sys.path.insert(0,str(Path(deps).resolve()))
import pytest
root=Path(__file__).resolve().parent
(root/'test-tmp').mkdir(exist_ok=True)
raise SystemExit(pytest.main(['-q',str(root/'tests'),'--basetemp='+str(root/'test-tmp'/str(time.time_ns())),'-o','cache_dir='+str(root/'test-cache')]))
