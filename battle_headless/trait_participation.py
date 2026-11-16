"""Original trait participant queries over an explicit battle snapshot.

This does not infer a full BattleParticipationStatus from a pair of units.
Callers must supply selection roles and the current attack's damaged IDs.
"""
from dataclasses import dataclass


ACTION_NAMES={0:'None',1:'Attack',2:'Counter',3:'Defense',4:'Evasion',
              5:'SupportAttack',6:'SupportCounter',7:'SupportDefense',8:'NoAction'}


@dataclass(frozen=True)
class TraitParticipation:
    main_attacker: object = None
    main_target: object = None
    support_defender: object = None
    attack_side_support_defender: object = None
    support_attackers: tuple = ()
    counter_supporters: tuple = ()
    counter_selection: int = 0
    active_attacker: object = None
    attack_side_damaged: object = None
    counter_side_damaged: object = None

    def contains(self, unit_id):
        return unit_id is not None and unit_id in (
            self.main_attacker,self.main_target,self.support_defender,
            self.attack_side_support_defender,*self.support_attackers,*self.counter_supporters)


def map_battle_action(unit_id, participation):
    """IngameTraitTarget.GetMapBattleAction, 0x6232004; preserves role priority."""
    if participation is None:return 0
    if unit_id is None:raise ValueError('A trait unit target needs a concrete unit ID')
    p=participation
    if unit_id==p.main_attacker:return 1
    if unit_id==p.main_target:return {1:2,3:3,2:4}.get(p.counter_selection,8)
    if unit_id in p.support_attackers:return 5
    if unit_id in p.counter_supporters:return 6
    if unit_id in (p.support_defender,p.attack_side_support_defender):return 7
    return 0


def is_active_attacker(unit_id, participation):
    """IngameTraitTarget.IsActiveAttacker -> BattleParticipationStatus."""
    return (participation is not None and participation.active_attacker is not None
            and unit_id==participation.active_attacker)


def is_attack_target(unit_id, owner_id, participation, *, owner_is_unit=True):
    """IngameTraitTarget.IsAttackTarget, full 0x6230594 switch.

    The owner must be this strike's active attacker. Its selection role chooses
    the damaged side; unrelated participants and the owner itself never match.
    """
    if participation is None or not participation.contains(unit_id):return False
    if not owner_is_unit or owner_id==unit_id:return False
    if not is_active_attacker(owner_id,participation):return False
    action=map_battle_action(owner_id,participation)
    if action in (1,5):return unit_id==participation.counter_side_damaged
    if action in (2,6):return unit_id==participation.attack_side_damaged
    return False
