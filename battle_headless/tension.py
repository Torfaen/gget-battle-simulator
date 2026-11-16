"""IngameUnitEntity.GetCurrentTension 0x613e00c, including thunk continuation."""
from .original import OriginalScalarKernel,i32


def current_tension(unit,kernel=OriginalScalarKernel):
    overflow=i32(unit.get('scalar_corrections',{}).get('MpLimitBreakCount',0))>0
    return kernel.tension(unit['mp'],True) if overflow else kernel.tension(unit['mp'])
