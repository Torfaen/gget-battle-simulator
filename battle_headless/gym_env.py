"""Optional Gymnasium / sb3-contrib MaskablePPO integration."""
import gymnasium as gym
import numpy as np
from .rl import BattleRL, make_factory
from .skill_effects import ACTIVE_SKILL_KINDS


class GundamEnv(gym.Env):
    def __init__(self, profile='account-hard', factory=None, **kwargs):
        super().__init__()
        self.battle = BattleRL(factory or make_factory(profile), **kwargs)
        self.action_space = gym.spaces.Discrete(len(self.battle.actions))
        self.observation_space = gym.spaces.Dict({
            'units': gym.spaces.Box(-np.inf, np.inf, (len(self.battle.unit_ids), 16), np.float32),
            'skill_buffs': gym.spaces.Box(-np.inf, np.inf, (len(self.battle.unit_ids), len(ACTIVE_SKILL_KINDS), 2), np.float32),
            'chance_steps': gym.spaces.Box(-np.inf, np.inf, (len(self.battle.unit_ids), 6), np.float32),
            'moment_trigger': gym.spaces.MultiBinary(3),
            'clock': gym.spaces.Box(-np.inf, np.inf, (2,), np.float32),
            'action_mask': gym.spaces.MultiBinary(len(self.battle.actions)),
        })

    @staticmethod
    def _convert(obs):
        return {key: np.asarray(value, dtype=np.int8 if key in ('action_mask','moment_trigger') else np.float32)
                for key, value in obs.items()}

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if seed is None:
            seed = int(self.np_random.integers(0, 2**32))
        obs, info = self.battle.reset(seed=seed, options=options)
        return self._convert(obs), info

    def step(self, action):
        action = int(action)
        mask = self.battle.action_masks()
        invalid = not 0 <= action < len(mask) or not mask[action]
        # Generic Gym agents/checkers need a defined transition for every ID.
        # Invalid choices end the player's turn, with an additional penalty.
        obs, reward, terminated, truncated, info = self.battle.step(0 if invalid else action)
        info['invalid_action'] = invalid
        if invalid:
            reward -= 0.1
        return self._convert(obs), reward, terminated, truncated, info

    def action_masks(self):
        return np.asarray(self.battle.action_masks(), dtype=bool)
