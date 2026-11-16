"""Recovered support accounting and character-skill compilation for client 2.7.0.

These are independent primitives, not the complete services/target selectors.
"""
from dataclasses import dataclass, asdict
from copy import deepcopy
from .original import i32, round_down, div


def short(value):
    return (int(value)+32768) % 65536-32768


@dataclass
class SupportBudget:
    attack_limit: int = 0
    defense_limit: int = 0
    attack_adaptive_limit: int = 0
    defense_adaptive_limit: int = 0
    attack_once: int = 0
    defense_once: int = 0
    attack_used: int = 0
    defense_used: int = 0
    attack_adaptive_used: int = 0
    defense_adaptive_used: int = 0
    total_attack_used: int = 0
    total_defense_used: int = 0

    def support_type(self):
        if i32(self.attack_limit+self.attack_adaptive_limit+short(self.attack_once)) > 0:
            return 1
        return 2 if i32(self.defense_limit+self.defense_adaptive_limit+short(self.defense_once)) > 0 else 0

    def current(self):
        kind = self.support_type()
        if not kind:
            return 0
        prefix='attack' if kind==1 else 'defense'
        value=short(getattr(self,prefix+'_used'))
        if getattr(self,prefix+'_adaptive_limit') >= 1:
            value += short(getattr(self,prefix+'_adaptive_used'))
        return i32(value)

    def maximum(self):
        kind=self.support_type()
        if not kind:
            return 0
        prefix='attack' if kind==1 else 'defense'
        return i32(getattr(self,prefix+'_limit')+getattr(self,prefix+'_adaptive_limit')+short(getattr(self,prefix+'_once')))

    def can_attack(self):
        return self.support_type()==1 and self.current()<self.maximum()

    def can_defend(self):
        return self.support_type()==2 and self.current()<self.maximum()

    def consume(self, kind):
        """Port of UseSupportAttackAndCounter/UseSupportDefense; returns timing ID."""
        if kind not in ('attack','defense'):
            raise ValueError('Expected attack or defense')
        adaptive=getattr(self,kind+'_adaptive_used')
        used=getattr(self,kind+'_used')
        once=getattr(self,kind+'_once')
        timing=0
        if getattr(self,kind+'_adaptive_limit') > short(adaptive):
            setattr(self,kind+'_adaptive_used',short(adaptive+1))
        elif getattr(self,kind+'_limit') > short(used):
            setattr(self,kind+'_used',short(used+1))
        elif short(once) >= 1:
            setattr(self,kind+'_once',short(once-1))
            timing=16 if kind=='attack' else 17
        else:
            raise ValueError('Original support budget exhausted')
        field='total_'+kind+'_used'
        setattr(self,field,(getattr(self,field)+1+2**63)%2**64-2**63)
        return timing

    def reset(self):
        self.attack_used=self.defense_used=0
        self.attack_adaptive_used=self.defense_adaptive_used=0


# Read from the native lookup tables at 0x27a35cc and 0x27a3550.
SKILL_TRAIT_TYPES=(3,5,72,43,1,2,70,17,21,23,29,62,49,50,47,3,14,14,17,17,17,51,52,19,19,51,52,6,100,101,102)
SKILL_TIMINGS=(0,0,7,0,3,3,8,3,3,3,3,3,6,9,3,0,3,10,3,3,3,3,3,3,15,16,17,0,6,6,0)


def compile_skill_trait(kind, value, duration):
    if not 1 <= kind <= 31 or not ((0x7fff7fff >> (kind-1)) & 1):
        raise NotImplementedError(f'Client rejects character skill trait type {kind}')
    timing=SKILL_TIMINGS[kind-1]
    return dict(character_skill_trait_type=kind,trait_type=SKILL_TRAIT_TYPES[kind-1],
                value=-value if kind==15 else value,action_timing=timing,
                limit=0 if timing==0 else value if kind in (25,26,27) else duration,
                timing_requires_override=not bool((0x77ff7fff >> (kind-1)) & 1))


def character_skill_definition(master, skill_id):
    row=master.index['m_character_skill'][skill_id]
    ids=[r['CharacterSkillTraitId'] for r in master.tables['m_character_skill_trait_set']
         if r['Id']==row['CharacterSkillTraitSetId']]
    effects=[]
    for trait_id in ids:
        source=master.index['m_character_skill_trait'][trait_id]
        effect=compile_skill_trait(source['TraitTypeIndex'],source['TraitValue'],row['Duration'])
        effect['source']=deepcopy(source)
        effects.append(effect)
    return dict(source=deepcopy(row),effects=effects,
                use_conditions=deepcopy([r for r in master.tables['m_character_skill_use_condition']
                    if r['CharacterSkillId']==skill_id]),
                scope='Compiled skill definition; target/effect execution requires skill services')


def npc_passive_definitions(master, unit_row, character_row):
    """NPC factories use dedicated NPC ability lists; zero is not normal-unit fallback."""
    tables=master.tables
    from .trait_sources import npc_initial_sources
    sources=npc_initial_sources(master,None,unit_row,character_row)
    traits=[]
    for source in sources:
        ability=master.index['m_ability'][source['source_id']]
        for trait_link in tables['m_trait_set']:
            if trait_link['Id']==ability['TraitSetId']:
                row=deepcopy(master.index['m_trait'][trait_link['TraitId']])
                row['ability_id']=ability['Id']
                row['conditions']={field:deepcopy([c for c in tables['m_trait_condition']
                    if c['TraitConditionSetId']==row[field]])
                    for field in ['ActiveConditionSetId','TargetConditionSetId']}
                traits.append(row)
    return traits


def character_stat(base, rate=0, flat=0):
    """UserCharacterStatus._CalcStatus 0x61939ac; base already includes growth."""
    correction=round_down(div(i32(base*rate),100))
    return min(99999,max(0,i32(base+flat+correction)))


def predicted_hp_damage(hp_total_damage_taken, hp_damage_taken, current_hp_barrier):
    """AICounterRuleBehaviorBase.CalcPredictedHpDamage, used by counter AI."""
    return max(hp_damage_taken,max(0,i32(hp_total_damage_taken-current_hp_barrier)))


def attach_stage_passives(env, stage):
    """Apply only the verified unconditional owner traits used by this stage.

    Conditional/timed traits remain explicit in each unit's pending list.
    This does not yet execute support battles or refresh their phase counts.
    """
    npcs={n['placement']['Id']:n for n in stage.npcs}
    summary=[]
    for unit in env.units.values():
        npc=npcs[unit['source_npc_id']]
        traits=npc_passive_definitions(stage.master,npc['units'][0],npc['characters'][0])
        budget=SupportBudget()
        rates={27:0,29:0}
        applied,pending=[],[]
        for trait in traits:
            conditions=trait['conditions']['TargetConditionSetId']
            simple=(trait['ActionTimingTypeIndex']==0 and not trait['ActiveConditionSetId']
                    and trait['TargetConditionSetId']==1000001 and len(conditions)==1
                    and conditions[0]['TargetTypes']=='Owner')
            kind,value=trait['TraitTypeIndex'],trait['TraitValue']
            if simple and kind in (27,29,51,52):
                if kind in rates:
                    rates[kind]+=value
                elif kind==51:
                    budget.attack_limit+=value
                else:
                    budget.defense_limit+=value
                applied.append(trait['Id'])
            else:
                pending.append(trait['Id'])
        # Always recompute from the original pilot snapshot, so reattachment is idempotent.
        pilot=npc['characters'][0]
        unit['pilot']['Reaction']=character_stat(pilot['Reaction'],rates[27])
        unit['pilot']['Awaken']=character_stat(pilot['Awaken'],rates[29])
        unit['support_budget']=asdict(budget)
        unit['passive_traits']=traits
        unit['pending_passive_ids']=pending
        unit['applied_passive_ids']=applied
        unit['character_skills']=[r['CharacterSkillId'] for r in stage.master.tables['m_map_npc_character_skill_set_content']
                                 if r['MapNpcCharacterSkillSetId']==pilot['MapNpcCharacterSkillSetId']]
        summary.append(dict(unit=unit['id'],applied=applied,pending=pending,
                            support_type=budget.support_type(),skills=unit['character_skills']))
    env.rules.name='original-scalar-mt19937-partial-owner-passives'
    return summary
