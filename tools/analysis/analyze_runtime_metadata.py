"""Decode v31 metadata declarations. Layout checked against Il2CppDumper MetadataClass.cs."""

import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from battle_paths import OUTPUTS, RESOURCE_CACHE

class Metadata:
    def __init__(self,path):
        self.data=Path(path).read_bytes()
        magic,version=struct.unpack_from('<II',self.data)
        if magic!=0xfab11baf or version!=31: raise ValueError('Expected standard metadata v31')
        self.tables=[struct.unpack_from('<II',self.data,8+i*8) for i in range(31)]
        self.strings=self.section(2)
        self.types=list(self.records(19,'<16i8H2I'))
        self.methods=list(self.records(5,'<7i4H'))
        self.parameters=list(self.records(10,'<3i'))
        self.fields=list(self.records(11,'<3i'))
    def section(self,index):
        offset,size=self.tables[index]
        if offset+size>len(self.data): raise ValueError('Invalid metadata section')
        return self.data[offset:offset+size]
    def records(self,index,fmt):
        blob=self.section(index)
        if len(blob)%struct.calcsize(fmt): raise ValueError(f'Invalid table stride {index}')
        return struct.iter_unpack(fmt,blob)
    def string(self,index):
        if index<0 or index>=len(self.strings): return None
        return self.strings[index:self.strings.find(b'\0',index)].decode('utf-8','replace')
    def describe(self,index):
        t=self.types[index]
        methods=[]
        for i in range(t[9],t[9]+t[16]):
            m=self.methods[i]
            if m[1]!=index: raise ValueError('Method ownership mismatch')
            methods.append(dict(index=i,name=self.string(m[0]),token=hex(m[6]),return_type_index=m[2],
                parameters=[dict(name=self.string(p[0]),type_index=p[2]) for p in self.parameters[m[4]:m[4]+m[10]]]))
        return dict(index=index,name=self.string(t[0]),namespace=self.string(t[1]),
            type_index=t[2],methods=methods,fields=[dict(name=self.string(f[0]),type_index=f[1]) for f in self.fields[t[8]:t[8]+t[18]]])

    def find_type(self,namespace,name):
        matches=[i for i,t in enumerate(self.types) if self.string(t[0])==name and self.string(t[1])==namespace]
        if len(matches)!=1:raise ValueError(f'Expected one type: {namespace}.{name}; found {len(matches)}')
        return matches[0]

def main():
    meta=Metadata(RESOURCE_CACHE/'mapped-metadata-0.dat')
    wanted=re.compile(r'UnitDevelopmentDiagramKoma|UnitDevelopmentDiagramArea|UnitSprite|KomaAsset|SpriteLoader|UnitImage|AssetPath|AssetLoader|UnitResource',re.I)
    classes=[meta.describe(i) for i,t in enumerate(meta.types) if wanted.search(meta.string(t[0]) or '')]
    literals=[]
    literal_data=meta.section(1)
    for length,offset in meta.records(0,'<II'):
        value=literal_data[offset:offset+length].decode('utf-8','replace')
        if re.search(r'(?:unit|koma).*(?:sprite|png|texture)|(?:sprite|texture).*(?:unit|koma)',value,re.I) and len(value)<1000:
            literals.append(value)
    report=dict(version=31,type_count=len(meta.types),method_count=len(meta.methods),classes=classes,literals=literals)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    (OUTPUTS/'runtime_metadata_clues.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for c in classes:
        print(c['index'],c['namespace']+'.'+c['name'])
        print('  fields',[(f['name'],f['type_index']) for f in c['fields']])
        print('  methods',[(m['name'],[p['name'] for p in m['parameters']]) for m in c['methods']])
    print('LITERALS',literals[:50])

if __name__=='__main__': main()
