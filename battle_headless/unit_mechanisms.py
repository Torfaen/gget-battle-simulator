"""Current-form master mechanisms for the calculator's fixed-stage subset.

CanEscape's no-mechanism branch needs no MP, escape form or placement query.
The positive escape branch is deliberately not approximated here.
"""


def mechanism_rows(master, unit_id):
    unit = master.index['m_unit'][unit_id]
    set_id = unit['MechanismSetId']
    links = [row for row in master.tables['m_mechanism_set']
             if row['MechanismSetId'] == set_id]
    if set_id != 0 and not links:
        raise ValueError(f'Missing mechanism set {set_id} for unit {unit_id}')
    return [master.index['m_mechanism'][row['MechanismId']]
            for row in sorted(links, key=lambda row: row['SortOrder'])]


def can_escape_without_mechanism(master, unit):
    """Resolve the current unit_id each time; reject a positive type-4 source.

    This binding is valid only while unit_id identifies the entity's current
    MasterUnit. Form-changing callers must update that identity first.
    """
    if any(row['MechanismTypeIndex'] == 4
           for row in mechanism_rows(master, unit['unit_id'])):
        raise NotImplementedError('Escape mechanism requires original MP, form and placement services')
    return False
