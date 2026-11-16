import unittest
from battle_headless.map_context import power_resources
from battle_headless.map_weapon_data import properties
from battle_headless.map_weapon_runtime import use_map_weapon


class MapPowerResourceTests(unittest.TestCase):
    def test_payment_changes_current_resources_but_not_battle_power_inputs(self):
        unit=dict(hp=100,max_hp=100,en=40,max_en=40,mp=6,max_mp=12,
                  battle_start_hp=100,battle_start_en=40)
        weapon=dict(main_weapon_id=1,weapon_traits=[],map_level_stats=dict(
            power=1000,hit=100,critical=0,hp_recovery_rate=0,en_recovery_rate=0,mp_recovery_value=0))
        records=[dict(HighHpBoost=100,HighEnBoost=100)]
        before=properties(power_resources(unit),weapon,records)['power']
        use_map_weapon(unit,weapon,[],en_cost=20,hp_cost=50,mp_cost=2)
        self.assertEqual((unit['hp'],unit['en'],unit['pending_mp_change']),(50,20,-2))
        self.assertEqual(properties(power_resources(unit),weapon,records)['power'],before)
        self.assertLess(properties(unit,weapon,records)['power'],before)

    def test_nullable_fallback_zero_snapshot_and_current_mp(self):
        unit=dict(hp=10,en=20,mp=5,battle_start_hp=0,battle_start_en=None)
        result=power_resources(unit)
        self.assertEqual((result['hp'],result['en'],result['mp']),(0,20,5))
        self.assertEqual(unit['hp'],10)
        del unit['battle_start_hp'];self.assertEqual(power_resources(unit)['hp'],10)


if __name__=='__main__':unittest.main()
