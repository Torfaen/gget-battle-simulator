import unittest
from battle_headless.attack_prediction import AttackPrediction, compare_predictions


class PredictionTests(unittest.TestCase):
    def prediction(self, damage=1000, hp=10000, hit=100, en=20, order=0, danger=False):
        return AttackPrediction(damage,hp,hit,en,order,danger)

    def test_safety_precedes_damage(self):
        self.assertLess(compare_predictions(self.prediction(damage=1),
            self.prediction(damage=10000,danger=True)),0)

    def test_cost_and_hit_can_outweigh_power(self):
        self.assertLess(compare_predictions(self.prediction(damage=1000,hit=100,en=10),
            self.prediction(damage=1500,hit=50,en=30)),0)

    def test_overkill_is_capped_before_priority(self):
        self.assertGreater(compare_predictions(self.prediction(damage=100000,hp=100,en=30),
            self.prediction(damage=100,hp=100,en=10)),0)

    def test_tie_uses_signed_short_and_keeps_exact_equality(self):
        self.assertEqual(compare_predictions(self.prediction(order=-32768),self.prediction(order=32767)),-65535)
        a=self.prediction()
        self.assertEqual(compare_predictions(a,a),0)


if __name__=='__main__':unittest.main()
