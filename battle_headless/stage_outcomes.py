"""Connect KillAll/TurnCount stage outcomes, using native condition grouping.

IngameBattleConditionChecker checks failure groups before success groups.
Each group is AND, groups are OR; turn predicates receive Number - 1.
HistoricMoment uses the same supported predicates; scripts remain separate.
"""
from .unit_life import is_alive
from .original import i32


class StageOutcomes:
    def __init__(self, env, conditions, side_teams):
        self.env = env
        self.side_teams = dict(side_teams)
        self.groups = {1: {}, 2: {}, 3: {}, 4: {}}
        for row in conditions:
            category = row['MapStageConditionTypeIndex']
            if category not in self.groups:
                raise NotImplementedError('Unsupported stage condition category')
            if any(row.get(k) for k in ('IngameEventConditionParameterTypeBIndex', 'ParameterValueB',
                                       'IngameEventConditionParameterTypeCIndex', 'ParameterValueC')):
                raise NotImplementedError('Additional stage condition parameters')
            kind = row['IngameEventConditionTypeIndex']
            if kind == 1:
                if row['IngameEventConditionParameterTypeAIndex'] != 5 or int(row['ParameterValueA']) not in side_teams:
                    raise NotImplementedError('KillAll requires a mapped BattleSide')
            elif kind != 7:
                raise NotImplementedError('Unsupported stage condition predicate')
            self.groups[category].setdefault(row['MapStageConditionGroupId'], []).append(dict(row))
        if not self.groups[1]:
            raise ValueError('No stage victory condition')
        # Native GroupPair constructor derives Exists from the success group.
        self.has_moment_conditions = bool(self.groups[3])
        if self.has_moment_conditions:
            self.env.moment_trigger['exists'] = True
        self.env.moment_condition_result = None

    def evaluate(self, row):
        if row['IngameEventConditionTypeIndex'] == 7:
            return row['TargetValue'] <= i32(self.env.turn - 1)
        team = self.side_teams[int(row['ParameterValueA'])]
        return not any(is_alive(u) and u['team'] == team for u in self.env.units.values())

    def check_moment(self):
        """Condition-only headless transition; no script or reinforcement callbacks.

        Native checker 0x5f04cbc checks failure before success, and
        GroupPair.ShouldBeCheckedCondition stops after either result is latched.
        """
        state = self.env.moment_trigger
        if (not self.has_moment_conditions or not state.get('exists') or
                state.get('is_success') or state.get('is_failure')):
            return self.env.moment_condition_result
        for category in (4, 3):
            for group_id, rows in self.groups[category].items():
                if all(self.evaluate(row) for row in rows):
                    state['is_success'] = category == 3
                    state['is_failure'] = category == 4
                    self.env.moment_condition_result = dict(
                        result='success' if category == 3 else 'failure',
                        group_id=group_id, condition_ids=[r['Id'] for r in rows],
                        turn=self.env.turn, profile='kill-all-turn-count-subset')
                    return self.env.moment_condition_result
        return None

    def check(self):
        if getattr(self.env, 'stage_outcome', None) is not None:
            return self.env.stage_outcome
        self.check_moment()
        for category in (2, 1):
            for group_id, conditions in self.groups[category].items():
                if not all(self.evaluate(row) for row in conditions):
                    continue
                result = dict(result='defeat' if category == 2 else 'victory',
                    group_id=group_id, condition_ids=[row['Id'] for row in conditions],
                    turn=self.env.turn, elapsed_turns=i32(self.env.turn - 1),
                    predicate_types=[row['IngameEventConditionTypeIndex'] for row in conditions],
                    profile='kill-all-turn-count-subset')
                self.env.stage_outcome = result
                self.env.terminated = True
                self.env.truncated = False
                self.env.winner = self.side_teams[2 if category == 2 else 0]
                return result
        return None


def attach_stage_outcomes(env, stage):
    if env.phase_clock is None:
        raise ValueError('Stage outcome checks require the original standard turn clock')
    runtime = StageOutcomes(env, stage.conditions, {0: 'player', 2: 'enemy'})
    env.stage_outcome = None
    env._check_stage_outcome = runtime.check
    env.stage_outcome_runtime = runtime
    return runtime
