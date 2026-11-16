"""Original engagement buckets and replacing pair-count log entries."""
from dataclasses import dataclass,asdict
from .original import i32
from .trait_factory import i64


@dataclass(frozen=True)
class EngagementLog:
    turn: int
    attacker: object
    target: object
    after_historic_moment: bool


@dataclass(frozen=True)
class PairCountLog:
    user_unit_id: int
    target_id: int
    count: int


class BattleActivityLogs:
    def __init__(self):
        self.engagements={};self.used_weapons={};self.attack_supports={};self.defense_supports={}

    def add_engagement(self,turn,attacker,target,after_historic_moment):
        log=EngagementLog(i32(turn),attacker,target,bool(after_historic_moment))
        self.engagements.setdefault(log.turn,[]).append(log)

    def _increment(self,table,user,target,count):
        key=(i64(user),i64(target));old=table.get(key)
        table[key]=PairCountLog(*key,i32((0 if old is None else old.count)+i32(count)))

    def add_used_weapon(self,user,weapon):self._increment(self.used_weapons,user,weapon,1)
    def add_attack_support(self,user,target):self._increment(self.attack_supports,user,target,1)
    def add_defense_support(self,user,target,count):self._increment(self.defense_supports,user,target,count)

    def snapshot(self):
        return dict(engagements=[dict(turn=k,logs=[asdict(r) for r in v]) for k,v in self.engagements.items()],
            **{name:[asdict(r) for r in getattr(self,name).values()]
               for name in ('used_weapons','attack_supports','defense_supports')})
