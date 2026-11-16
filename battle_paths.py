"""Resolve local inputs and generated files outside the source checkout."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_ENV = 'GGET_BATTLE_DATA_DIR'
DEFAULT_DATA_ROOT = PROJECT_ROOT.with_name(f'{PROJECT_ROOT.name}-local')
DATA_ROOT = Path(os.environ.get(DATA_ENV) or DEFAULT_DATA_ROOT).expanduser().resolve()
if DATA_ROOT.is_relative_to(PROJECT_ROOT):
    raise ValueError(f'{DATA_ENV} must point outside the source checkout: {PROJECT_ROOT}')

ARTIFACTS = DATA_ROOT / 'original_battle_reuse/artifacts'
OUTPUTS = DATA_ROOT / 'outputs'
RESOURCE_CACHE = DATA_ROOT / '.resource_cache'
MASTER_SNAPSHOT = DATA_ROOT / 'investigations/20261016_update'
