"""Self recovery, owner buffs and limited action effects; full services pending.

Uses compiled native trait bindings and master SP settings. This is an explicit
subset of availability/target/execution services, not the full client executor.
"""
from .unit_life import is_alive
from .combat_primitives import resource_percent
from .weapon_use import reflect_mp_change
from .mechanics import character_skill_definition
from .skill_effects import TIMED_SKILL_KINDS, LIMITED_SKILL_TIMINGS, ACTIVE_SKILL_KINDS, attach_skill_effects


def unsupported_reason(definition, *, timed=False):
    row = definition['source']
    if definition['use_conditions']:
        return 'skill use-condition predicates pending'
    if row['RangeTypeIndex'] != 0 or row['TargetTypeIndex'] != 0:
        return 'area/selected skill targets pending'
    if row['En'] or row['Mp']:
        return 'skill EN/MP cost contexts pending'
    if not definition['effects']:
        return 'no effects'
    for effect in definition['effects']:
        source = effect['source']
        if any(source[k] for k in ('TargetLineageId', 'TargetUnitRoleTypeIndex', 'TargetCharacterRoleTypeIndex')):
            return 'skill recipient filters pending'
        kind = effect['character_skill_trait_type']
        if (timed and kind in TIMED_SKILL_KINDS and effect['action_timing'] == 3
                and effect['limit'] > 0 and not effect['timing_requires_override']):
            continue
        if (timed and kind in LIMITED_SKILL_TIMINGS and effect['action_timing'] == LIMITED_SKILL_TIMINGS[kind]
                and effect['limit'] > 0 and not effect['timing_requires_override']):
            if kind in (25,26,27) and effect['limit'] > 32767:
                return 'one-time count exceeds native signed-short range'
            continue
        if kind not in (1, 2, 4) or effect['value'] < 0:
            return 'timed/non-recovery skill effect pending'
    return None


class RecoverySkills:
    def __init__(self, env, definitions, timed_runtime=None):
        self.env = env
        self.definitions = definitions
        self.timed_runtime = timed_runtime

    def reason(self, definition):
        return unsupported_reason(definition, timed=self.timed_runtime is not None)

    def catalog(self):
        return [dict(type='skill', unit=uid, skill=sid)
                for uid, skills in self.definitions.items()
                for sid, definition in skills.items() if not self.reason(definition)]

    def available(self, unit, skill_id):
        definition = self.definitions[unit['id']][skill_id]
        row = definition['source']
        if self.reason(definition) or unit['team'] != self.env.active_team or not is_alive(unit):
            return False
        if unit['acted'] or unit.get('scalar_corrections', {}).get('ActionInabilityCount', 0) > 0:
            return False
        if unit['sp'] < row['Sp']:
            return False
        if row['UsageLimit'] > 0 and unit['skill_uses'].get(skill_id, 0) >= row['UsageLimit']:
            return False
        kinds = {e['character_skill_trait_type'] for e in definition['effects']}
        if 14 in kinds and not hasattr(self.env, '_execute_exchange'):
            return False
        if 4 in kinds:
            corrections = unit.get('scalar_corrections', {})
            if ('mp' not in unit or unit.get('pending_mp_change', 0)
                    or any(corrections.get(field, 0) > 0 for field in
                           ('AntiMpChangeCount', 'MpLimitBreakCount'))):
                return False  # Modified/queued MP lifecycle remains pending.
        if 25 in kinds:
            state = unit.get('chance_step', {})
            if (not hasattr(self.env, '_after_attack') or state.get('body_type') != 1
                    or state.get('once', 0) != 0):
                return False  # Original combination with other one-time sources is pending.
        for kind, field in ((26, 'attack_once'), (27, 'defense_once')):
            if kind in kinds:
                hook = 'support_exchange' if kind == 26 else 'support_defense'
                if not hasattr(self.env, hook) or unit.get('support_budget', {}).get(field, 0) != 0:
                    return False
        timed = [e for e in definition['effects'] if e['character_skill_trait_type'] in ACTIVE_SKILL_KINDS]
        if any(entry['trait']['TraitTypeIndex'] == e['trait_type'] for e in timed
               for entry in unit.get('active_skill_effects', [])):
            return False  # Original priority/replacement across overlapping buffs is pending.
        return bool(timed) or any((unit['mp'] < 12 if e['character_skill_trait_type'] == 4 else
                   unit['hp' if e['character_skill_trait_type'] == 1 else 'en'] <
                   unit['max_hp' if e['character_skill_trait_type'] == 1 else 'max_en'])
                   and e['value'] > 0 for e in definition['effects'])

    def actions(self):
        return [action for action in self.catalog()
                if self.available(self.env.units[action['unit']], action['skill'])]

    def execute(self, action):
        unit = self.env.units[action['unit']]
        sid = action['skill']
        if not self.available(unit, sid):
            raise ValueError('Character skill is unavailable')
        definition = self.definitions[unit['id']][sid]
        before = dict(hp=unit['hp'], en=unit['en'], sp=unit['sp'], mp=unit.get('mp',0))
        effects = []
        for index, effect in enumerate(definition['effects']):
            if effect['character_skill_trait_type'] in ACTIVE_SKILL_KINDS:
                effects.append(self.timed_runtime.add(unit, sid, effect, index))
                continue
            if effect['character_skill_trait_type'] == 4:
                old = unit['mp']
                unit['pending_mp_change'] = effect['value']
                reflect_mp_change(unit)
                effects.append(dict(resource='mp', amount=unit['mp']-old, requested_amount=effect['value']))
                continue
            resource = 'hp' if effect['character_skill_trait_type'] == 1 else 'en'
            old = unit[resource]
            amount = resource_percent(unit['max_' + resource], effect['value'])
            unit[resource] = min(unit['max_' + resource], old + amount)
            effects.append(dict(resource=resource, amount=unit[resource] - old))
        unit['sp'] -= definition['source']['Sp']
        unit['skill_uses'][sid] = unit['skill_uses'].get(sid, 0) + 1
        unit['last_character_skill'] = sid
        return dict(skill=sid, effects=effects, resource_before=before,
                    resource_after=dict(hp=unit['hp'], en=unit['en'], sp=unit['sp'], mp=unit.get('mp',0)),
                    skill_execution_profile='owner-recovery-turn-buff-subset')


def attach_character_skills(env, master):
    configs = {r['ConfigTypeIndex']: r['Value'] for r in master.tables['m_config']}
    # ConfigType enum: SpAtStageBattleStart=77, CharacterMaxSp=163.
    maximum, initial = int(configs[163]), int(configs[77])
    definitions = {}
    timed_runtime = attach_skill_effects(env, master)
    for uid, unit in env.units.items():
        unit['max_sp'] = maximum
        unit['sp'] = max(0, min(maximum, initial))
        unit['skill_uses'] = {}
        owned_once = sum(e['remaining'] for e in unit.get('active_skill_effects', []) if e['skill_kind']==25)
        if owned_once and 'chance_step' in unit:
            unit['chance_step']['once'] = max(0, unit['chance_step']['once']-owned_once)
        for kind, field in ((26, 'attack_once'), (27, 'defense_once')):
            owned = sum(e['remaining'] for e in unit.get('active_skill_effects', []) if e['skill_kind']==kind)
            if owned and 'support_budget' in unit:
                unit['support_budget'][field] = max(0, unit['support_budget'].get(field, 0)-owned)
        unit['active_skill_effects'] = []
        unit['pending_character_skills'] = []
        skills = {}
        configuration = unit.get('account_configuration')
        # NPC skills require their own level/SP policy; do not infer account defaults.
        if configuration:
            for row in configuration['character_skill_configuration']:
                sid = row['CharacterSkillId']
                if not sid or row['RequiredLevel'] > configuration['character_level']:
                    continue
                if (configuration.get('character_specialized') and row.get('SpCharacterSkillId')
                        and row['SpCharacterSkillId'] != sid):
                    unit['pending_character_skills'].append(dict(id=sid, reason='specialized skill selection pending'))
                    continue
                definition = character_skill_definition(master, sid)
                skills[sid] = definition
                reason = unsupported_reason(definition, timed=timed_runtime is not None)
                if reason:
                    unit['pending_character_skills'].append(dict(id=sid, reason=reason))
        definitions[uid] = skills
    runtime = RecoverySkills(env, definitions, timed_runtime)
    env.character_skill_runtime = runtime
    env._skill_actions = runtime.actions
    env._execute_skill = runtime.execute
    env.skill_action_catalog = runtime.catalog()
    return runtime
