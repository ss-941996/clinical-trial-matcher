"""Runtime paths are configurable; collection/model details come from the bundle."""
import json
import math
import os
from pathlib import Path


def load_settings():
    default=Path(__file__).parent/'config.json'
    path=Path(os.environ.get('TRIAL_CONFIG_PATH',str(default))).resolve()
    config=json.loads(default.read_text(encoding='utf-8'))
    overrides=json.loads(path.read_text(encoding='utf-8')) if path!=default.resolve() else {}
    config.update(overrides)
    config['default_method']=os.environ.get('TRIAL_PIPELINE',config['default_method'])
    if config['default_method'] not in {'hybrid','fusion','bm25'}:raise ValueError('Invalid configured pipeline')
    for name in ['semantic_rrf_weight','fusion_balance_weight']:
        value=config.get(name)
        if (value is None and name=='semantic_rrf_weight') or value is not None and (type(value) not in {float,int} or not math.isfinite(value) or value<=0):
            raise ValueError(f'{name} must be finite and positive')
    # File paths follow their config file; environment paths follow the cwd.
    def location(env,key,fallback=None):
        if os.environ.get(env):return str(Path(os.environ[env]).resolve())
        if key in config:return str(((path.parent if key in overrides else default.parent)/config[key]).resolve())
        return fallback
    bundle=location('TRIAL_HYBRID_BUNDLE','hybrid_bundle')
    hybrid=config['default_method']=='hybrid'
    if hybrid and not bundle:raise ValueError('Configure hybrid_bundle or TRIAL_HYBRID_BUNDLE')
    index=location('TRIAL_INDEX_PATH','trial_index',str(Path(bundle)/'bm25.sqlite') if hybrid else str(default.parents[2]/'data/indexes/bm25.sqlite'))
    return config,index,bundle if hybrid else None
