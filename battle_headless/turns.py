"""Original standard phase cycle and chance-step state transitions.

Stage event scheduling, phase-fixed league turns and terrain movement remain
separate services; this module does not substitute them with a two-side clock.
"""
from .unit_life import is_alive
from dataclasses import asdict,dataclass
from .original import i32
from .mechanics import short
from .mechanics import SupportBudget
from .combat_primitives import resource_percent

# BattleSideTypeExtensions.NextPhaseBattleSideType, native table 0x27a188c.
NEXT_PHASE={0:4,1:4,2:5,3:6,4:2,5:3,6:0,7:0}


@dataclass(frozen=True)
class PhaseValue:
    turn:int
    id:int

    def is_turn_progressed(self,next_value):
        return self.turn<next_value.turn and self.id==next_value.id


@dataclass
class OriginalTurnClock:
    turn:int=1
    side:int=0
    transitions:int=0

    def advance(self):
        if self.side not in NEXT_PHASE:raise ValueError('Invalid original battle side')
        self.side=NEXT_PHASE[self.side]
        if self.side==0:self.turn=i32(self.turn+1)
        self.transitions+=1
        return PhaseValue(self.turn,self.side)


@dataclass
class ChanceStepState:
    body_type:int=1
    existence:int=0
    used:int=0
    correction:int=0
    once:int=0
    guaranteed_once:int=0
    guaranteed:bool=False
    weapon_used_guaranteed:bool=False
    has_moved:bool=False
    can_move_after_attack:bool=False
    can_move_after_map_attack:bool=False

    def maximum(self):
        return i32(1+self.correction+short(self.once)+short(self.guaranteed_once)) if self.body_type==1 else 0

    def remaining(self):return max(0,i32(self.maximum()-short(self.used)))

    def init_phase(self):
        self.has_moved=self.can_move_after_attack=self.can_move_after_map_attack=False
        if self.existence in (0,1):self.existence=0;self.used=0

    def _eligible(self):
        # Native bit mask 0x2f1 over existence 2..11. Do not reinterpret holes.
        return self.body_type==1 and self.existence not in {2,6,7,8,9,11}

    def try_weapon_used(self,target_defeated):
        """Return (earned, consumed timing), matching TryUseChanceStepWeaponUsed."""
        if not self._eligible():return False,0
        normal=i32(self.correction+1);timing=0
        if target_defeated and normal>short(self.used):self.used=short(self.used+1)
        elif target_defeated and short(self.once)>0:
            self.once=short(self.once-1);timing=15
        elif not target_defeated and (self.guaranteed or self.weapon_used_guaranteed) and normal>short(self.used):
            self.used=short(self.used+1)
        elif short(self.guaranteed_once)>0:
            self.guaranteed_once=short(self.guaranteed_once-1);timing=23
        else:return False,0
        self.can_move_after_attack=False
        return True,timing

    def try_guaranteed(self):
        if not self._eligible():return False,0
        timing=0
        if self.guaranteed and i32(self.correction+1)>short(self.used):self.used=short(self.used+1)
        elif short(self.guaranteed_once)>0:
            self.guaranteed_once=short(self.guaranteed_once-1);timing=23
        else:return False,0
        self.can_move_after_map_attack=False
        return True,timing

    def end_step(self):
        """IngameUnitEntity.EndStep 0x613cc50, including unusual states."""
        self.can_move_after_attack=self.can_move_after_map_attack=False
        if not (0<=self.existence<=11 and (0xbe6 & (1<<self.existence))):
            self.existence=1

    def end_map_weapon_attack_step(self):
        """0x613cd64: preserve special states through EndStep, else reopen."""
        if not self.can_move_after_map_attack or (
                0<=self.existence<=11 and (0xbc4 & (1<<self.existence))):
            self.end_step()
        else:self.existence=0

    def end_attack_step(self):
        """IngameUnitEntity.EndAttackStep 0x613cce0, ordinary move permission."""
        if not self.can_move_after_attack or (
                0<=self.existence<=11 and (0xbc4 & (1<<self.existence))):
            self.end_step()
        else:self.existence=0


def _unit_state(unit):
    state=ChanceStepState(**unit['chance_step'])
    corrections=unit.get('scalar_corrections',{})
    state.correction=corrections.get('ChanceStepMaxCount',0)
    state.guaranteed=corrections.get('GuaranteedChanceStepCount',0)>0
    state.weapon_used_guaranteed=corrections.get('GuaranteedChanceStepWeaponUsedOnlyCount',0)>0
    return state


def init_unit_phase(unit):
    if 'chance_step' not in unit:
        if is_alive(unit):unit['acted']=unit['moved']=False
        return
    state=_unit_state(unit)
    if not is_alive(unit):state.existence=2
    elif unit['acted'] and state.existence==0:state.existence=1
    state.init_phase();unit['chance_step']=asdict(state)
    if state.existence==0:unit['acted']=unit['moved']=False


def after_attack(unit,target,event,*,consume_chance_step=None):
    state=_unit_state(unit)
    state.existence=1 if is_alive(unit) else 2
    state.has_moved=unit['moved']
    earned,timing=state.try_weapon_used(not is_alive(target))
    if earned:
        # A new step starts from the unit's current cell. Phase-level movement
        # history remains in state.has_moved; the flat-grid step flag is reset.
        unit['acted']=unit['moved']=False
        state.existence=0
    unit['chance_step']=asdict(state)
    event['chance_step']=dict(earned=earned,consumed_timing=timing,
        used=state.used,remaining=state.remaining())
    if earned and consume_chance_step is not None:
        event['chance_step']['trait_consumption']=consume_chance_step(unit,timing)


def ordinary_phase_recovery(env,stage):
    clock=getattr(env,'phase_clock',None)
    if not clock or clock.side not in (0,1) or env.turn==1:return []
    # ExecuteAsync -> IIngameUnitRepository.FindAliveOnMap, including enemies.
    # OwnPhaseStartUseCase supplies m_config.EnRecoveryRatePerTurn (6), flat=0.
    configs={r['ConfigTypeIndex']:r['Value'] for r in stage.master.tables['m_config']}
    recovery_rate=int(configs.get(6,'0'));effects=[]
    for unit in env.units.values():
        if not is_alive(unit):continue
        before=unit['en'];amount=resource_percent(unit['max_en'],recovery_rate)
        unit['en']=max(0,min(unit['max_en'],before+amount))
        budget=SupportBudget(**unit.get('support_budget',{}));budget.reset()
        unit['support_budget']=asdict(budget)
        effects.append(dict(unit=unit['id'],source='ordinary_own_phase_start',
            en_before=before,en_after=unit['en'],en_amount=unit['en']-before,support_counts_reset=True))
    return effects


def attach_original_actions(env,stage):
    for unit in env.units.values():
        definition=stage.master.index['m_unit'][unit['unit_id']]
        unit['chance_step']=asdict(ChanceStepState(body_type=definition['UnitBodyTypeIndex']))
    env._after_attack=lambda unit,target,event:after_attack(unit,target,event,
        consume_chance_step=getattr(env,'consume_runtime_chance_step',None))
    env.original_actions=True
    env._after_phase_start=lambda:(ordinary_phase_recovery(env,stage)+
        (env._phase_passive_effects() if hasattr(env,'_phase_passive_effects') else []))
