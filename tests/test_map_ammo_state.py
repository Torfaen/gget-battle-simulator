from copy import deepcopy
import json
import unittest
from battle_headless.map_weapon_runtime import (used_ammo,remaining_ammo,unit_has_remaining_ammo,
    consume_map_ammo,use_map_weapon,recover_map_ammo,can_recover_map_ammo)

class MapAmmoStateTests(unittest.TestCase):
 def unit(self):return dict(hp=100,max_hp=100,en=40,max_en=60,mp=6)
 def weapon(self,identity=1,main=100000000001):return dict(id=identity,main_weapon_id=main)
 def test_finite_use_pays_resources_and_shares_main_id_across_variants(self):
  unit=self.unit();first,second=self.weapon(),self.weapon(2)
  self.assertEqual(remaining_ammo(unit,first,2,[]),2)
  result=use_map_weapon(unit,first,[],en_cost=7,mp_cost=2,hp_cost=5)
  self.assertEqual((unit['hp'],unit['en'],unit['mp'],unit['pending_mp_change']),(95,33,6,-2))
  self.assertEqual(result['map_ammo']['used_after'],1)
  self.assertEqual(used_ammo(unit,second),1)
  use_map_weapon(unit,second,[],en_cost=7,mp_cost=None,hp_cost=None)
  self.assertFalse(unit_has_remaining_ammo(unit,first,2,[]))
  self.assertEqual(remaining_ammo(unit,first,2,[]),0)
 def test_unlimited_keeps_count_but_pays_resources_and_display_stays_finite(self):
  unit=self.unit();weapon=self.weapon();unlimited=[dict(AmmoUnlimitedCount=1)]
  use_map_weapon(unit,weapon,unlimited,en_cost=7,mp_cost=1,hp_cost=2)
  self.assertNotIn('map_weapon_used_counts',unit)
  self.assertEqual((unit['hp'],unit['en'],unit['pending_mp_change']),(98,33,-1))
  consume_map_ammo(unit,weapon,[])
  self.assertEqual(remaining_ammo(unit,weapon,1,unlimited),0)
  self.assertTrue(unit_has_remaining_ammo(unit,weapon,1,unlimited))
  use_map_weapon(unit,weapon,unlimited,en_cost=7,mp_cost=1,hp_cost=2)
  self.assertEqual(used_ammo(unit,weapon),1)
  self.assertFalse(unit_has_remaining_ammo(unit,weapon,1,[]))
 def test_prediction_skips_all_mutation(self):
  unit=self.unit();before=deepcopy(unit)
  result=use_map_weapon(unit,self.weapon(),[],en_cost=7,mp_cost=2,hp_cost=5,predicting=True)
  self.assertEqual(unit,before);self.assertEqual(result,dict(resource_use_skipped=True))
 def test_recovery_changes_existing_keys_without_capacity_or_new_entries(self):
  unit=self.unit();first,second=self.weapon(),self.weapon(2,200000000001)
  for _ in range(3):consume_map_ammo(unit,first,[])
  consume_map_ammo(unit,second,[])
  changes=recover_map_ammo(unit,2)
  self.assertEqual((used_ammo(unit,first),used_ammo(unit,second)),(1,0))
  self.assertEqual(len(changes),2);self.assertEqual(unit['en'],40)
  recover_map_ammo(unit,-4)
  self.assertEqual((used_ammo(unit,first),used_ammo(unit,second)),(5,4))
  blank=self.unit();self.assertEqual(recover_map_ammo(blank,10),[])
  self.assertNotIn('map_weapon_used_counts',blank)
 def test_recoverability_only_considers_supplied_all_form_weapons(self):
  unit=self.unit();first,other=self.weapon(),self.weapon(2,200000000001)
  consume_map_ammo(unit,first,[])
  self.assertFalse(can_recover_map_ammo(unit,[]))
  self.assertFalse(can_recover_map_ammo(unit,[other]))
  self.assertTrue(can_recover_map_ammo(unit,[first,other]))
  recovered=json.loads(json.dumps(unit))
  self.assertEqual(used_ammo(recovered,self.weapon(999)),1)
 def test_native_signed_overflow_and_zero_floor(self):
  unit=self.unit();weapon=self.weapon();key=str(weapon['main_weapon_id'])
  unit['map_weapon_used_counts']={key:2**31-1}
  consume_map_ammo(unit,weapon,[])
  self.assertEqual(used_ammo(unit,weapon),-(2**31))
  self.assertEqual(remaining_ammo(unit,weapon,1,[]),0)
  recover_map_ammo(unit,1)
  self.assertEqual(used_ammo(unit,weapon),2**31-1)

if __name__=='__main__':unittest.main()
