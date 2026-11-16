"""Owner skill corrections with separate turn, weapon and movement consumption."""
TIMED_SKILL_KINDS = (5, 6, 8, 9, 10, 11, 12, 15)
LIMITED_SKILL_TIMINGS = {3: 7, 7: 8, 13: 6, 30: 6, 25: 15, 26: 16, 27: 17, 14: 9}
ACTIVE_SKILL_KINDS = TIMED_SKILL_KINDS + tuple(LIMITED_SKILL_TIMINGS)


def skill_buff_observation(unit):
    rows = [[0.0, 0.0] for _ in ACTIVE_SKILL_KINDS]
    for entry in unit.get('active_skill_effects', []):
        index = ACTIVE_SKILL_KINDS.index(entry['skill_kind'])
        rows[index] = [entry['trait']['TraitValue'] / 100,
                       entry['remaining'] / max(1, entry['duration'])]
    return rows


class SkillEffects:
    def __init__(self, env, master):
        self.env = env
        self.template = {key: '' if isinstance(value, str) else 0
                         for key, value in master.tables['m_trait'][0].items()}
        self.last_turn = env.turn

    def add(self, unit, skill_id, effect, index):
        trait = dict(self.template, Id=-(skill_id * 100 + index + 1),
            TraitTypeIndex=effect['trait_type'], TraitValue=effect['value'],
            TargetConditionSetId=1000001)
        entry = dict(skill=skill_id, skill_kind=effect['character_skill_trait_type'],
            trait=trait, action_timing=effect['action_timing'],
            remaining=effect['limit'], duration=effect['limit'])
        unit['active_skill_effects'].append(entry)
        if entry['skill_kind'] == 25:
            unit['chance_step']['once'] += entry['remaining']
        elif entry['skill_kind'] in (26, 27):
            field = 'attack_once' if entry['skill_kind'] == 26 else 'defense_once'
            unit['support_budget'][field] += entry['remaining']
        return dict(trait_type=trait['TraitTypeIndex'], value=trait['TraitValue'],
                    remaining=entry['remaining'], action_timing=entry['action_timing'])

    def turn_progress(self, record):
        elapsed = max(0, record['turn'] - self.last_turn)
        if not elapsed:
            return
        self.last_turn = record['turn']
        expired = []
        for unit in self.env.units.values():
            active = []
            for entry in unit.get('active_skill_effects', []):
                if entry['action_timing'] != 3:
                    active.append(entry)
                    continue
                entry['remaining'] = max(0, entry['remaining'] - elapsed)
                if entry['remaining']:
                    active.append(entry)
                else:
                    expired.append(dict(unit=unit['id'], skill=entry['skill'], trait_id=entry['trait']['Id']))
            unit['active_skill_effects'] = active
        if expired:
            record['expired_character_skill_effects'] = expired
        self.env._refresh_state()

    def consume(self, unit, timings, *, first_only=False):
        consumed, active = [], []
        for entry in unit.get('active_skill_effects', []):
            if entry['action_timing'] in timings and (not first_only or not consumed):
                entry['remaining'] = max(0, entry['remaining'] - 1)
                consumed.append(dict(skill=entry['skill'], trait_id=entry['trait']['Id'],
                    action_timing=entry['action_timing'], remaining=entry['remaining']))
            if entry['remaining'] > 0:
                active.append(entry)
        unit['active_skill_effects'] = active
        if consumed:
            self.env._refresh_state()
        return consumed


def attach_skill_effects(env, master):
    if not hasattr(env, '_refresh_state') or env.phase_clock is None:
        return None
    if hasattr(env, 'skill_effect_runtime'):
        env.skill_effect_runtime.last_turn = env.turn
        return env.skill_effect_runtime
    runtime = SkillEffects(env, master)
    previous = getattr(env, '_after_turn_progress', None)
    def progress(record):
        if previous is not None:
            previous(record)
        runtime.turn_progress(record)
    env._after_turn_progress = progress
    env.skill_effect_runtime = runtime
    return runtime
