"""Version-local IL2CPP native method resolver. Offline, read-only inputs."""
import io
import struct
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from battle_paths import DATA_ROOT

sys.path.insert(0, str(DATA_ROOT / '.resource_runtime'))
from elftools.elf.elffile import ELFFile
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from tools.analysis.analyze_runtime_metadata import Metadata


def compressed_int(blob, offset):
    first = blob[offset]
    if first < 0x80:
        unsigned = first
    elif first < 0xc0:
        unsigned = ((first & 0x7f) << 8) | blob[offset+1]
    elif first < 0xe0:
        unsigned = ((first & 0x3f) << 24) | int.from_bytes(blob[offset+1:offset+4], 'big')
    elif first == 0xf0:
        unsigned = int.from_bytes(blob[offset+1:offset+5], 'little')
    elif first == 0xfe:
        unsigned = 0xfffffffe
    elif first == 0xff:
        unsigned = 0xffffffff
    else:
        raise ValueError(f'Unsupported compressed integer prefix {first:#x}')
    return -(unsigned >> 1)-1 if unsigned & 1 else unsigned >> 1


def enum_values(meta, index):
    defaults = {f: d for f, _, d in meta.records(7, '<3i')}
    blob, t = meta.section(8), meta.types[index]
    return {meta.string(meta.fields[i][0]): compressed_int(blob, defaults[i])
            for i in range(t[8], t[8]+t[18]) if i in defaults and defaults[i] >= 0}


class Native:
    def __init__(self, apk, metadata):
        self.meta = Metadata(metadata)
        with zipfile.ZipFile(apk) as archive:
            self.data = archive.read('lib/arm64-v8a/libil2cpp.so')
        elf = ELFFile(io.BytesIO(self.data))
        self.segments = [s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
        self.reloc = {}
        for section in elf.iter_sections():
            if section['sh_type'] == 'SHT_RELA':
                for r in section.iter_relocations():
                    if r['r_info_type'] == 1027:
                        self.reloc[r['r_offset']] = r['r_addend']
        self.md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
        self.symbols, self.methods = {}, {}
        plt, relplt = elf.get_section_by_name('.plt'), elf.get_section_by_name('.rela.plt')
        if plt is not None and relplt is not None:
            symtab = elf.get_section(relplt['sh_link'])
            for idx, relocation in enumerate(relplt.iter_relocations()):
                name = symtab.get_symbol(relocation['r_info_sym']).name
                self.symbols[plt['sh_addr']+32+idx*16] = ['ELF.import.'+name]
        self.images = list(self.meta.records(20, '<10i'))
        self.modules = {}

    def offset(self, va):
        for s in self.segments:
            if s['p_vaddr'] <= va < s['p_vaddr']+s['p_filesz']:
                return s['p_offset']+va-s['p_vaddr']
        raise ValueError(hex(va))

    def ptr(self, va):
        return self.reloc[va] if va in self.reloc else struct.unpack_from('<Q', self.data, self.offset(va))[0]

    def module(self, name):
        if name in self.modules:
            return self.modules[name]
        needle = name.encode()+b'\0'
        positions, start = set(), 0
        while True:
            pos = self.data.find(needle, start)
            if pos < 0:
                break
            for s in self.segments:
                if s['p_offset'] <= pos < s['p_offset']+s['p_filesz']:
                    positions.add(pos-s['p_offset']+s['p_vaddr'])
            start = pos+1
        candidates = []
        for address, addend in self.reloc.items():
            if addend not in positions:
                continue
            try:
                count = struct.unpack_from('<I', self.data, self.offset(address+8))[0]
                table = self.ptr(address+16)
                if 10 < count < 1000000 and table:
                    self.offset(table)
                    candidates.append((count, table))
            except (ValueError, struct.error):
                pass
        if len(candidates) != 1:
            raise ValueError(f'Ambiguous codegen module {name}: {candidates}')
        self.modules[name] = candidates[0]
        return candidates[0]

    def index_image(self, name):
        count, table = self.module(name)
        image = next(i for i in self.images if self.meta.string(i[0]) == name)
        for idx in range(image[2], image[2]+image[3]):
            cls = self.meta.describe(idx)
            for method in cls['methods']:
                token = int(method['token'], 16) & 0xffffff
                if 0 < token <= count:
                    va = self.ptr(table+(token-1)*8)
                    if va:
                        label = f"{cls['namespace']}.{cls['name']}.{method['name']}"
                        self.symbols.setdefault(va, []).append(label)
                        self.methods[method['index']] = dict(method, rva=va, class_index=idx, label=label)

    def disassemble(self, method, limit=32768):
        va = method['rva']
        # Nearest higher method entry is a bound, not proof of function length.
        higher = [p for p in self.symbols if p > va]
        length = min(limit, min(higher)-va) if higher else limit
        offset = self.offset(va)
        instructions = []
        for ins in self.md.disasm(self.data[offset:offset+length], va):
            row = dict(address=hex(ins.address), mnemonic=ins.mnemonic, operands=ins.op_str)
            if ins.mnemonic in ('bl', 'b') and ins.op_str.startswith('#0x'):
                target = int(ins.op_str[1:], 16)
                row['target'] = self.symbols.get(target, [])
            instructions.append(row)
        # Remove obvious unrelated trailing code for functions without switch
        # dispatch. Indirect branches remain explicitly unresolved.
        by_address = {int(row['address'],16): row for row in instructions}
        pending, reachable, indirect = [va], set(), False
        while pending:
            address = pending.pop()
            if address in reachable or address not in by_address:
                continue
            reachable.add(address)
            row = by_address[address]
            mnemonic, operands = row['mnemonic'], row['operands']
            if mnemonic == 'ret' or (mnemonic == 'bl' and operands == '#0x57fb394'):
                continue
            if mnemonic == 'br':
                # Restored LR/SP before br indicates a virtual tail call.
                preceding = [by_address.get(address-i,{}).get('operands','') for i in [4,8]]
                if not any('x30' in op and '[sp]' in op for op in preceding):
                    indirect = True
                continue
            if mnemonic == 'b' or mnemonic.startswith('b.') or mnemonic in ('cbz','cbnz','tbz','tbnz'):
                target_text = operands.split(',')[-1].strip()
                if target_text.startswith('#0x'):
                    target = int(target_text[1:],16)
                    if target not in self.symbols or target == va:
                        pending.append(target)
                if mnemonic == 'b':
                    continue
            pending.append(address+4)
        if reachable and not indirect:
            instructions = [row for row in instructions if int(row['address'],16) <= max(reachable)]
        return dict(method, rva=hex(va), bound_bytes=length,
                    bound_is_exact=False, unresolved_indirect_dispatch=indirect,
                    instructions=instructions)

    def disassemble_cfg(self, method, max_instructions=4096):
        """Follow direct out-of-line branches without assuming contiguous code.

        Calls are recorded, not traversed. Known other method entries are tail
        boundaries; indirect branches are retained as unresolved boundaries.
        This is a bounded static graph, not a proof of complete native behavior.
        """
        entry = method['rva']
        pending, rows, boundaries = [entry], {}, []
        while pending:
            address = pending.pop()
            if address in rows:
                continue
            if address != entry and address in self.symbols:
                boundaries.append(dict(address=hex(address), kind='other_method_entry',
                                       targets=self.symbols[address]))
                continue
            if len(rows) >= max_instructions:
                raise ValueError('Native CFG instruction budget exceeded')
            offset = self.offset(address)
            ins = next(self.md.disasm(self.data[offset:offset+4], address), None)
            if ins is None:
                raise ValueError(f'Cannot decode reachable instruction {address:#x}')
            row = dict(address=hex(address), mnemonic=ins.mnemonic, operands=ins.op_str)
            rows[address] = row
            if ins.mnemonic in ('bl', 'b') and ins.op_str.startswith('#0x'):
                target = int(ins.op_str[1:], 16)
                row['target'] = self.symbols.get(target, [])
            if ins.mnemonic == 'ret':
                continue
            if ins.mnemonic == 'br':
                boundaries.append(dict(address=hex(address), kind='indirect_branch'))
                continue
            if ins.mnemonic == 'bl' and ins.op_str in ('#0x57fb394', '#0xd167280'):
                continue
            if ins.mnemonic == 'b' or ins.mnemonic.startswith('b.') or ins.mnemonic in ('cbz', 'cbnz', 'tbz', 'tbnz'):
                operand = ins.op_str.split(',')[-1].strip()
                if operand.startswith('#0x'):
                    pending.append(int(operand[1:], 16))
                if ins.mnemonic == 'b':
                    continue
            pending.append(address+4)
        return dict(method, rva=hex(entry), analysis='bounded-direct-control-flow',
                    bound_is_exact=False, boundaries=boundaries,
                    instructions=[rows[address] for address in sorted(rows)])
