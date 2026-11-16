"""Original battle snapshots and deferred resource reflection boundaries."""
from .original import i32
from .weapon_resource_damage import plus_en
from contextlib import contextmanager


def begin_battle(unit):
    # OnBattleStart captures MP independently of the nullable HP/EN snapshots.
    # Pending queues are deliberately not cleared by this method.
    try:hp,en,mp=(unit[key] for key in ('hp','en','mp'))
    except KeyError as error:
        raise NotImplementedError('Import HP/EN/MP before entering the original battle resource boundary') from error
    unit['battle_start_hp']=hp;unit['battle_start_en']=en
    unit['battle_start_mp']=mp
    unit['mp_change_since_battle_start']=0
    unit['has_participated_in_battle']=True


def end_battle(unit):
    # OnBattleEnd only clears nullable HP/EN; MP baseline/reflection persist.
    unit.pop('battle_start_hp',None);unit.pop('battle_start_en',None)


@contextmanager
def target_battle_resources(attacker,target,*,prediction=False):
    """MapWeaponExecutor._Attack: actual non-self target start/end boundary.

    Native compares IngameUnitIds using inequality before both callbacks.
    Prediction uses Begin/EndPrediction instead and never changes these fields.
    """
    separate=not prediction and target['id']!=attacker['id']
    if separate:begin_battle(target)
    try:yield
    finally:
        if separate:end_battle(target)


def reflect_en_change(unit):
    """ReflectEnChangeValue 0x61420d4; immediate EN damage is separate."""
    delta=unit.get('pending_en_change',0)
    if not delta:return 0
    applied=plus_en(unit,delta)
    unit['pending_en_change']=0
    return applied


def mp_change_info(unit):
    """GetMpChangeSinceBattleStart 0x61424ac: requested vs clamped change."""
    pending=unit.get('pending_mp_change',0)
    projected=max(0,min(12,i32(unit['mp']+pending)))
    return dict(requested_change=i32(unit.get('mp_change_since_battle_start',0)+pending),
                effective_change=i32(projected-unit['battle_start_mp']))
