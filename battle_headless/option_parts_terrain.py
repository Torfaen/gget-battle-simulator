"""Original option-parts trait-set extraction, separate from battle traits."""
from .terrain_capability import TERRAIN_CHANGE_TYPES,change_record


def extract_changes(traits):
    return [change_record(trait) for trait in traits if trait['TraitTypeIndex'] in TERRAIN_CHANGE_TYPES]


def parts_changes(master,part_ids):
    # UserUnitEntity returns null for an empty equipped-parts list, but an empty
    # allocated list for nonempty parts without terrain conversions.
    if not part_ids:return None
    result=[]
    for part_id in part_ids:
        trait_set=master.index['m_option_parts'][part_id]['TraitSetId']
        traits=[master.index['m_trait'][link['TraitId']] for link in master.tables['m_trait_set'] if link['Id']==trait_set]
        result.extend(dict(row,source_option_parts_id=part_id) for row in extract_changes(traits))
    return result
