"""Original HP/EN/MP-dependent weapon power correction arithmetic."""
import math
from .original import f32,add,sub,mul,div,i32,round_up,round_down
from .weapon_corrections import sum_field,percentage_delta

def ieee_div(a,b):
    a,b=f32(a),f32(b)
    if math.isnan(a) or math.isnan(b):return math.nan
    if b==0:
        return math.nan if a==0 else math.copysign(math.inf,a*math.copysign(1,b))
    return div(a,b)

def resource_boost(base,current,maximum,low,high):
    # Native prefers any nonzero low boost; high is not added to low.
    boost=low if low else high
    if not boost:return 0
    fraction=mul(ieee_div(current,maximum),100)
    if low:fraction=sub(100,fraction)
    value=mul(ieee_div(add(ieee_div(fraction,ieee_div(100,boost)),100),100),base)
    return i32((round_up(value) if low else round_down(value))-base)

def mp_boost(base,current,maximum,low,high):
    boost=low if low else high
    if not boost:return 0
    amount=i32(maximum-current) if low else current
    return round_down(ieee_div(mul(mul(ieee_div(boost,maximum),amount),base),100))

def distance_boost(base,base_min,base_max,extreme_max,distance,proximity,remoteness):
    if proximity:
        numerator=i32(i32(extreme_max-distance+1)*proximity)
        fraction=ieee_div(numerator,i32(extreme_max-base_min+1))
    elif remoteness:
        fraction=ieee_div(i32(remoteness*distance),base_max)
        # Native FCSEL only caps on ordered greater-than (NaN stays NaN).
        if fraction>f32(remoteness):fraction=f32(remoteness)
    else:return 0
    return round_up(ieee_div(mul(fraction,base),100))


def effective_power(unit,weapon,records,*,distance=None,extreme_max=None):
    base=weapon['power'];deltas=[]
    for stem in ('Hp','En'):
        deltas.append(resource_boost(base,unit[stem.lower()],unit['max_'+stem.lower()],
            sum_field(records,'Low'+stem+'Boost',weapon),sum_field(records,'High'+stem+'Boost',weapon)))
    deltas.append(mp_boost(base,unit['mp'],unit.get('max_mp',12),
        sum_field(records,'LowMpBoost',weapon),sum_field(records,'HighMpBoost',weapon)))
    if distance is not None and extreme_max is not None:
        deltas.append(distance_boost(base,weapon['range_min'],weapon['range_max'],extreme_max,distance,
            sum_field(records,'ProximityValue',weapon),sum_field(records,'RemotenessValue',weapon)))
    deltas.append(percentage_delta(records,'PowerRate','PowerValue',base,weapon))
    result=base
    for value in deltas:result=i32(result+value)
    return max(1,min(99999,result))
