"""Original initial unit/step counters and the separate non-unit owner sequence."""
from .original import i32
from .mechanics import short
from .trait_factory import i64
from .native_priority_sort import priority_sort


class InitialIdentitySequencer:
    def __init__(self):
        self.unit_counter=0
        self.step_counter=0
        self.non_unit_counter=0

    def next_unit_id(self):
        self.unit_counter=i32(self.unit_counter+1)
        return self.unit_counter

    def next_player_step_order(self):
        self.step_counter=i32(self.step_counter+1)
        return i32(self.step_counter+1000)

    def next_non_unit_owner_id(self):
        self.non_unit_counter=i32(self.non_unit_counter-1)
        return self.non_unit_counter


def npc_step_order(sort_order,group_formation_id):
    value=i32(sort_order)
    return value if value else i32(short(group_formation_id)*10+2000)


def npc_creation_order(npcs):
    def compare(a,b):
        left,right=i64(a['placement']['Id']),i64(b['placement']['Id'])
        return (left>right)-(left<right)
    return priority_sort(npcs,compare=compare)
