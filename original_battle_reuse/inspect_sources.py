"""Inventory the original battle entry points; never execute or modify game code."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import sys

from prepare_android import ARTIFACTS
from battle_paths import DATA_ROOT, RESOURCE_CACHE

sys.path.insert(0, str(DATA_ROOT / '.resource_runtime'))
from tools.analysis.battle_native import Native
from elftools.elf.elffile import ELFFile

FOCUS = {
    'AttackUseCase', 'AttackService', 'BattleCalculateService', 'IngameUnitEntity',
    'NpcIngameUnitEntity', 'UserIngameUnitEntity', 'IngameContextEntity',
    'IngameUnitRepository', 'IngameContextRepository', 'MapRootRepository',
    'IngameTraitSetService', 'IngameMainLoop', 'IngameMainLoopProcedure',
    'OrdinaryIngameMainLoopProcedure', 'IngameBattleContext', 'IngameBattleContextEntity',
    'MapRoot', 'MersenneTwister', 'ApplibotRandom', 'IngameMementoManager',
    'IngamePresentationService', 'IngameView', 'BattleScoreService',
    'AttackUseCasePlayer1AttackResult', 'AttackUseCasePlayer1SupportResult',
    'AttackUseCasePlayer2CounterResult', 'AttackUseCasePlayer2CounterSupportResult',
    'InMemoryIngameUnitRepository', 'InMemoryIngameContextRepository',
    'InMemoryMapRootRepository', 'IngameTurnActivity', 'TurnActivity',
}


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def inspect(out: Path):
    audit = json.loads((RESOURCE_CACHE / 'latest_game_audit.json').read_text(encoding='utf-8'))
    apk = Path(audit['raw_export']) / 'APK/split_config.arm64_v8a.apk'
    metadata = RESOURCE_CACHE / 'mapped-metadata-0.dat'
    native = Native(apk, metadata)
    if hashlib.sha256(native.data).hexdigest() != '1c969de7afd9903cfde5deed29a2050987c6e7dbb4dc2b257c1614beac27b443':
        raise ValueError('Only the pinned 2.7.0 native library is supported by this experiment.')
    elf = ELFFile(io.BytesIO(native.data))
    for image in ('Domain.dll', 'Application.dll', 'Infrastructure.dll', 'Presentation.dll'):
        native.index_image(image)
    out.mkdir(parents=True, exist_ok=True)
    classes = []
    attack_class = native.meta.find_type('Eternal.Application', 'AttackUseCase')
    attack_type_index = native.meta.types[attack_class][2]
    for index, definition in enumerate(native.meta.types):
        name = native.meta.string(definition[0])
        namespace = native.meta.string(definition[1]) or ''
        nested_attack = definition[3] == attack_type_index
        if not nested_attack and (name not in FOCUS or not namespace.startswith('Eternal.')):
            continue
        cls = native.meta.describe(index)
        methods = []
        for m in cls['methods']:
            address = native.methods.get(m['index'], {}).get('rva')
            methods.append(dict(m, rva=hex(address) if address else None,
                                entry_bytes=native.data[native.offset(address):native.offset(address)+16].hex() if address else None))
        cls['methods'] = methods
        if nested_attack:
            cls['declaring_class'] = 'Eternal.Application.AttackUseCase'
        cls['metadata_name_offset'] = native.meta.tables[2][0] + definition[0]
        classes.append(cls)
    counts = len(native.meta.types)
    registrations = []
    for address, pointer in native.reloc.items():
        try:
            if (struct.unpack_from('<Q', native.data, native.offset(address - 8))[0] == counts
                    and struct.unpack_from('<Q', native.data, native.offset(address + 8))[0] == counts):
                registrations.append(pointer)
        except (ValueError, struct.error):
            pass
    if len(registrations) != 1:
        raise ValueError(f'Ambiguous field-offset registration: {registrations}')
    layouts = {}
    for index, definition in enumerate(native.meta.types):
        namespace = native.meta.string(definition[1]) or ''
        if not namespace.startswith('Eternal.') or not definition[18]:
            continue
        pointer = native.ptr(registrations[0] + index * 8)
        offset = native.offset(pointer)
        layouts[namespace + '.' + native.meta.string(definition[0])] = {
            native.meta.string(native.meta.fields[definition[8] + j][0]):
                struct.unpack_from('<i', native.data, offset + j * 4)[0]
            for j in range(definition[18])}
    dynsym = elf.get_section_by_name('.dynsym')
    imports = sorted({s.name for s in dynsym.iter_symbols() if s['st_shndx'] == 'SHN_UNDEF' and s.name})
    exports = {s.name: hex(s['st_value']) for s in dynsym.iter_symbols()
               if s['st_shndx'] != 'SHN_UNDEF' and s.name.startswith('il2cpp_')}
    needed = [t.needed for t in elf.get_section_by_name('.dynamic').iter_tags() if t.entry.d_tag == 'DT_NEEDED']
    provenance = dict(apk=str(apk), metadata=str(metadata),
                      library_sha256=hashlib.sha256(native.data).hexdigest(),
                      metadata_sha256=hashlib.sha256(native.meta.data).hexdigest(),
                      machine=elf['e_machine'], elf_type=elf['e_type'],
                      classes=len(classes), layout_classes=len(layouts))
    # Both paths store state=-2. The second follows __cxa_end_catch. Capture
    # their distinct original instructions so async exceptions cannot pass.
    markers = []
    for role, address in [('success', 0x5ac99d4), ('exception', 0x5acfa70)]:
        offset = native.offset(address)
        instruction = next(native.md.disasm(native.data[offset:offset+4], address))
        if instruction.mnemonic != 'mov' or instruction.op_str != 'w8, #-2':
            raise ValueError('Pinned async terminal marker differs')
        markers.append(dict(role=role, rva=hex(address), entry_bytes=native.data[offset:offset+16].hex()))
    manifest = dict(schema_version=1, provenance=provenance, classes=classes,
                    il2cpp_exports=exports, needed_libraries=needed, imports=imports,
                    terminal_markers=markers,
                    limitations=['Static declarations and addresses are not execution evidence.',
                                 'No original exchange has been executed by this inventory.'])
    dump(out / 'source_manifest.json', manifest)
    dump(out / 'field_offsets.json', layouts)
    dump(out / 'standalone_static.json', dict(
        original_library_executed=False, independent_exchange_proven=False,
        machine=elf['e_machine'], needed_libraries=needed, imports=imports,
        assessment='Needs a compatible Android ARM64 loader, IL2CPP initialization and game object graph.',
        next_gate='Use offline.py to probe native loading and initialization. Live capture is an independent comparison path.'))
    print(json.dumps(dict(**provenance, needed_libraries=needed, il2cpp_exports=len(exports)), ensure_ascii=False))
    for cls in classes:
        if cls['name'] in {'AttackUseCase', 'IngameUnitRepository', 'IngameContextEntity', 'IngameContextRepository'}:
            print(json.dumps(dict(name=cls['namespace']+'.'+cls['name'], fields=cls['fields'],
                                  methods=[(m['name'], m['rva'], [p['name'] for p in m['parameters']]) for m in cls['methods']]), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ARTIFACTS / 'source')
    inspect(parser.parse_args().out)
