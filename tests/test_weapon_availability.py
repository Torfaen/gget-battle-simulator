from dataclasses import replace
import unittest
from battle_headless.weapon_availability import WeaponAvailabilityInputs,UnavailableReason as R,unavailable_reasons


class AvailabilityTests(unittest.TestCase):
    def state(self,**changes):
        return replace(WeaponAvailabilityInputs(100,30,30,5,None,2,0,0,True),**changes)

    def test_inability_short_circuits_and_action_precedes_attack(self):
        self.assertEqual(unavailable_reasons(self.state(action_inability=True,attack_inability=True,en=0)),(R.ActionInability,))
        self.assertEqual(unavailable_reasons(self.state(attack_inability=True,en=0)),(R.AttackInability,))

    def test_resource_equality_and_hp_survival_boundary(self):
        self.assertEqual(unavailable_reasons(self.state(mp_consumption=5,hp_consumption=99)),())
        self.assertEqual(unavailable_reasons(self.state(hp_consumption=100)),(R.NotEnoughHp,))
        self.assertEqual(unavailable_reasons(self.state(remaining_hp=0,hp_consumption=0)),())

    def test_all_failures_accumulate_in_original_order(self):
        self.assertEqual(unavailable_reasons(self.state(has_capability=False,en=0,mp_consumption=6,
            required_tension=5,hp_consumption=100,has_target=True,has_target_coord=True,
            within_range=False,damage_effective=False)),(R.HasNotCapability,R.NotEnoughEn,
            R.NotEnoughMp,R.NotEnoughTension,R.NotEnoughHp,R.OutOfRange,R.DamageEfficiencyIsZero))

    def test_target_and_coord_are_both_required_for_target_checks(self):
        for target,coord in [(False,False),(False,True),(True,False)]:
            self.assertEqual(unavailable_reasons(self.state(has_target=target,has_target_coord=coord,
                within_range=False,damage_effective=False)),())


if __name__=='__main__':unittest.main()
