"""TraitCorrection damage-cap collection semantics."""
def damage_cap(values):
    # Native collection minimum returns 0 for empty; no positive-only filter.
    # CalcTotalDamage applies the selected cap only when it is >=1.
    return min(values) if values else 0
