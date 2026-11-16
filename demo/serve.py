"""Run the local research demo; configuration is supplied through environment variables."""
import os
from pathlib import Path
import sys
sys.dont_write_bytecode=True
deps=os.environ.get('TRIAL_DEMO_DEPS')
if deps:sys.path.insert(0,str(Path(deps).resolve()))
import uvicorn
if __name__=='__main__':uvicorn.run('app.main:app',host=os.environ.get('HOST','127.0.0.1'),port=int(os.environ.get('PORT','8000')),workers=1,access_log=False)
