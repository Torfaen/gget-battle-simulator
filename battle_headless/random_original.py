"""Port of ApplibotLib.MersenneTwister / ApplibotRandom in client 2.7.0."""
from copy import deepcopy


class OriginalRandom:
    def __init__(self, seed=5489):
        self.mt = [0]*624
        if isinstance(seed, (list, tuple)):
            if not seed:
                raise ValueError('Seed array must not be empty')
            self.seed(19650218)
            i, j = 1, 0
            for _ in range(max(624,len(seed))):
                self.mt[i] = ((self.mt[i] ^ ((self.mt[i-1] ^ (self.mt[i-1] >> 30))*1664525)) + seed[j]+j) & 0xffffffff
                i, j = i+1, j+1
                if i >= 624:
                    self.mt[0], i = self.mt[623], 1
                if j >= len(seed):
                    j = 0
            for _ in range(623):
                self.mt[i] = ((self.mt[i] ^ ((self.mt[i-1] ^ (self.mt[i-1] >> 30))*1566083941))-i) & 0xffffffff
                i += 1
                if i >= 624:
                    self.mt[0], i = self.mt[623], 1
            self.mt[0] = 0x80000000
        else:
            self.seed(seed)

    def seed(self, seed):
        self.mt[0] = seed & 0xffffffff
        for i in range(1,624):
            self.mt[i] = (1812433253*(self.mt[i-1] ^ (self.mt[i-1] >> 30))+i) & 0xffffffff
        self.index = 624

    def uint32(self):
        if self.index >= 624:
            for i in range(624):
                y = (self.mt[i]&0x80000000) | (self.mt[(i+1)%624]&0x7fffffff)
                self.mt[i] = self.mt[(i+397)%624] ^ (y >> 1) ^ (0x9908b0df if y & 1 else 0)
            self.index = 0
        y = self.mt[self.index]
        self.index += 1
        y ^= y >> 11
        y ^= (y << 7) & 0x9d2c5680
        y ^= (y << 15) & 0xefc60000
        y ^= y >> 18
        return y & 0xffffffff

    def range_int(self, minimum, maximum):
        return minimum+int((maximum-minimum)*(1.0/4294967296.0)*self.uint32())

    def evaluate(self, rate):
        self.last_roll = None
        if rate < 1:
            return False
        if rate >= 100:
            return True
        self.last_roll = self.range_int(1,101)
        return self.last_roll <= rate

    def getstate(self):
        return deepcopy((self.index,self.mt))

    def setstate(self, state):
        self.index, self.mt = deepcopy(state)
