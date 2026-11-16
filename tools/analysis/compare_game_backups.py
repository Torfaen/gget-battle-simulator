"""Compare complete game backups using fresh version-specific metadata."""
import argparse
import base64
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from battle_paths import RESOURCE_CACHE
from tools.analysis.analyze_runtime_metadata import Metadata
from tools.analysis.resource_snapshot_formats import MessagePack,decode_octo_database

def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def write(p,value):p.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for part in iter(lambda:f.read(4*1024*1024),b''):h.update(part)
    return h.hexdigest()

def manifest(backup):
    state=read(backup/'备份说明.json');assert state['status']=='complete',str(backup)
    data={}
    for line in (backup/'文件清单.jsonl').read_text(encoding='utf-8').splitlines():
        r=json.loads(line)
        if 'sha256' in r:data[r['path'].replace('\\','/')]=r
    assert len(data)==state['files'] and sum(r['bytes'] for r in data.values())==state['bytes']
    return data

class MasterReader:
    def __init__(self,backup,metadata,key):
        self.root=backup/'外部数据/com.bandainamcoent.gget_WW/files/master_data'
        self.meta=Metadata(metadata);self.key=key;self.provenance=[]
        self.types={self.meta.string(t[0]).lower():i for i,t in enumerate(self.meta.types) if self.meta.string(t[1])=='Eternal.Infrastructure'}
    def plain(self,p):
        data=p.read_bytes();digest=hashlib.sha256(data).digest()
        assert base64.b32encode(digest).decode().rstrip('=')==p.stem,str(p)
        d=Cipher(algorithms.AES(self.key),modes.CBC(data[:16])).decryptor();plain=d.update(data[16:])+d.finalize();pad=plain[-1]
        assert 1<=pad<=16 and plain[-pad:]==bytes([pad])*pad,str(p)
        self.provenance.append(dict(path=p.relative_to(self.root).as_posix(),sha256=digest.hex()))
        return plain[:-pad]
    def table(self,name):
        files=list((self.root/'tables'/name).glob('*.bytes'))
        if not files:return [],None
        assert len(files)==1
        d=self.plain(files[0]);decoder=MessagePack(d);arrays=decoder.read();assert decoder.offset==len(d)
        assert isinstance(arrays,list)
        index=self.types.get(name.replace('_',''))
        if index is None:return arrays,None
        columns=[f['name'].split('>')[0][1:] for f in self.meta.describe(index)['fields'] if f['name'].startswith('<')]
        if not columns or not all(isinstance(r,list) and len(r)==len(columns) for r in arrays):return arrays,None
        return [dict(zip(columns,r)) for r in arrays],columns
    def languages(self):
        result={}
        for folder in sorted((self.root/'language_tables').iterdir()):
            files=list(folder.glob('*.json'));assert len(files)==1
            rows=json.loads(self.plain(files[0]))['data']
            result[folder.name]={str(r['id']):r['value'] for r in rows}
            assert len(result[folder.name])==len(rows)
        return result

def row_diff(a,b):
    if b and isinstance(b[0],dict) and 'Id' in b[0] and all(isinstance(r,dict) and 'Id' in r for r in a):
        aa={str(r['Id']):r for r in a};bb={str(r['Id']):r for r in b}
        if len(aa)==len(a) and len(bb)==len(b):
            return dict(added=[bb[k] for k in sorted(bb.keys()-aa.keys())],removed=[aa[k] for k in sorted(aa.keys()-bb.keys())],changed=[dict(id=k,fields=sorted(f for f in aa[k].keys()|bb[k].keys() if aa[k].get(f)!=bb[k].get(f)),before=aa[k],after=bb[k]) for k in sorted(aa.keys()&bb.keys()) if aa[k]!=bb[k]])
    if b and isinstance(b[0],list) and all(isinstance(r,list) and r and isinstance(r[0],(int,str)) for r in a+b):
        aa={str(r[0]):r for r in a};bb={str(r[0]):r for r in b}
        if len(aa)==len(a) and len(bb)==len(b):
            return dict(added=[bb[k] for k in sorted(bb.keys()-aa.keys())],removed=[aa[k] for k in sorted(aa.keys()-bb.keys())],changed=[dict(raw_first_column_key=k,before=aa[k],after=bb[k]) for k in sorted(aa.keys()&bb.keys()) if aa[k]!=bb[k]])
    aa=Counter(json.dumps(r,sort_keys=True) for r in a);bb=Counter(json.dumps(r,sort_keys=True) for r in b)
    return dict(added=[json.loads(r) for r in (bb-aa).elements()],removed=[json.loads(r) for r in (aa-bb).elements()],changed=[])

def cache_path(backup,item):
    prefix='R' if item['database_group']=='resourceList' else 'A'
    return backup/'内部数据/com.bandainamcoent.gget_WW/files/octo/v1/2001'/str(item['id']%10)/(prefix+str(item['id'])).encode().hex()/item['md5']

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True);parser.add_argument('--current',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--baseline-metadata',type=Path,default=RESOURCE_CACHE/'analysis_support/baseline_metadata.dat');parser.add_argument('--metadata',type=Path,required=True)
    parser.add_argument('--support',type=Path,default=RESOURCE_CACHE/'analysis_support')
    args=parser.parse_args();old=args.baseline.resolve();current=args.current.resolve();out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    a=manifest(old);b=manifest(current);added=sorted(b.keys()-a.keys());removed=sorted(a.keys()-b.keys());changed=sorted(k for k in a.keys()&b.keys() if a[k]['sha256']!=b[k]['sha256'])
    write(out/'file_comparison.json',dict(baseline=str(old),current=str(current),added=[b[k] for k in added],removed=[a[k] for k in removed],changed=[dict(before=a[k],after=b[k]) for k in changed]))
    octo=[];added_set=set(added)
    for k in added+changed:
        if '/files/octo/v1/' not in k or not re.fullmatch('[a-f0-9]{32}',Path(k).name):continue
        p=current/k;data=p.read_bytes();assert hashlib.md5(data).hexdigest()==p.name and hashlib.sha256(data).hexdigest()==b[k]['sha256']
        obj=bytes.fromhex(p.parent.name).decode('ascii');assert obj[0] in 'AR'
        octo.append(dict(path=k,object_name=obj,id=int(obj[1:]),size=len(data),md5=p.name,sha256=b[k]['sha256'],mtime=b[k].get('mtime'),header_hex=data[:16].hex(),change='added' if k in added_set else 'changed'))
    apk=[]
    for k in sorted(n for n in a.keys()|b.keys() if n.startswith('APK/')):
        if k in b:assert sha(current/k)==b[k]['sha256']
        if k in a:assert sha(old/k)==a[k]['sha256']
        apk.append(dict(path=k,baseline_sha256=a.get(k,{}).get('sha256'),current_sha256=b.get(k,{}).get('sha256'),changed=a.get(k,{}).get('sha256')!=b.get(k,{}).get('sha256')))
    file_summary=dict(baseline=str(old),current=str(current),baseline_files=len(a),current_files=len(b),added_files=len(added),removed_files=len(removed),changed_files=len(changed),unchanged_files=len(a.keys()&b.keys())-len(changed),new_or_changed_octo_files=len(octo),new_or_changed_octo_bytes=sum(r['size'] for r in octo),apk=apk,apk_identical=not any(r['changed'] for r in apk))
    write(out/'file_summary.json',file_summary);write(out/'new_octo_files.json',octo)
    print('FILE DIFF',len(added),len(changed),'OCTO',len(octo),flush=True)
    relative='内部数据/com.bandainamcoent.gget_WW/files/octo/pdb/2001/903646401/octocacheevai';key=(args.support/'octo_aes_key.bin').read_bytes()
    old_db=decode_octo_database(old/relative,key);new_db=decode_octo_database(current/relative,key);before=old_db['items'];after=new_db['items']
    delta=dict(baseline_revision=old_db['revision'],current_revision=new_db['revision'],baseline_resources=len(before),current_resources=len(after),groups=new_db['groups'],added=[after[n] for n in sorted(after.keys()-before.keys())],removed=[before[n] for n in sorted(before.keys()-after.keys())],changed=[dict(before=before[n],after=after[n]) for n in sorted(after.keys()&before.keys()) if after[n]['md5']!=before[n]['md5']],baseline_db_sha256=old_db['source_sha256'],current_db_sha256=new_db['source_sha256'])
    write(out/'resource_index_comparison.json',delta);write(out/'all_resource_index.json',after)
    selected=[]
    for kind,items in [('added',delta['added']),('changed',[r['after'] for r in delta['changed']])]:
        for r in items:
            p=cache_path(current,r);cached=p.is_file()
            if cached:
                data=p.read_bytes();assert len(data)==r['size'] and hashlib.md5(data).hexdigest()==r['md5']
            selected.append(dict(r,index_change=kind,cached=cached,cache_relative_path=p.relative_to(current).as_posix()))
    write(out/'indexed_asset_findings.json',selected)
    print('INDEX',len(before),'->',len(after),'ADDED',len(delta['added']),'CHANGED',len(delta['changed']),flush=True)
    key=(args.support/'master_aes_key.bin').read_bytes();mr_old=MasterReader(old,args.baseline_metadata,key);mr=MasterReader(current,args.metadata,key)
    names=sorted({p.name for p in (mr_old.root/'tables').iterdir()}|{p.name for p in (mr.root/'tables').iterdir()})
    tables={};diffs=[];schema_changes=[]
    for name in names:
        aa,ac=mr_old.table(name);bb,bc=mr.table(name);tables[name]=bb
        if ac!=bc:schema_changes.append(dict(table=name,baseline_columns=ac,current_columns=bc))
        if (ac is None)!=(bc is None):
            if ac is not None:aa=[list(r.values()) for r in aa]
            if bc is not None:bb=[list(r.values()) for r in bb]
        result=dict(table=name,before_rows=len(aa),rows=len(bb),baseline_schema_fields=len(ac) if ac else None,schema_fields=len(bc) if bc else None,**row_diff(aa,bb))
        diffs.append(result)
        if name in ['m_unit','m_character','m_supporter','m_event','m_gasha']:print('MASTER',name,len(aa),'->',len(bb),'NEW',len(result['added']),flush=True)
    langs=mr.languages();old_langs=mr_old.languages();ld=[]
    for name in sorted(old_langs.keys()|langs.keys()):
        aa=old_langs.get(name,{});bb=langs.get(name,{})
        ld.append(dict(table=name,added={k:bb[k] for k in sorted(bb.keys()-aa.keys())},removed={k:aa[k] for k in sorted(aa.keys()-bb.keys())},changed={k:dict(before=aa[k],after=bb[k]) for k in sorted(aa.keys()&bb.keys()) if aa[k]!=bb[k]}))
    named=[]
    for table_name in ['m_unit','m_character','m_supporter','m_warship','m_series','m_event','m_gasha']:
        d=next((r for r in diffs if r['table']==table_name),None)
        if not d:continue
        for row in d['added']:
            if not isinstance(row,dict):continue
            named.append(dict(table=table_name,id=str(row.get('Id','')),translations={k:langs.get(table_name,{}).get(str(v),'') for k,v in row.items() if 'LanguageId' in k},row=row))
    write(out/'parsed_tables.json',tables);write(out/'languages.json',langs);write(out/'master_comparison.json',diffs);write(out/'master_schema_changes.json',schema_changes);write(out/'language_comparison.json',ld);write(out/'new_named_entries.json',named)
    write(out/'master_provenance.json',dict(baseline=mr_old.provenance,current=mr.provenance,baseline_metadata_sha256=sha(args.baseline_metadata),current_metadata_sha256=sha(args.metadata)))
    master_summary=dict(tables=len([n for n in names if (mr.root/'tables'/n).exists()]),language_tables=len(langs),changed_tables=sum(any(r[k] for k in ['added','removed','changed']) for r in diffs),schema_changed_tables=len(schema_changes),new_table_names=[n for n in names if not (mr_old.root/'tables'/n).exists()],language_added_strings=sum(len(r['added']) for r in ld),language_changed_strings=sum(len(r['changed']) for r in ld))
    write(out/'master_summary.json',master_summary)
    portraits={n:r for n,r in after.items() if n.startswith(('ub_','ogk_'))};old_portraits={n:r for n,r in before.items() if n.startswith(('ub_','ogk_'))}
    unit_links={}
    for u in tables.get('m_unit',[]):
        if not isinstance(u,dict):continue
        for n in ['ub_'+u['BromideResourceId'],'ogk_'+u['BromideResourceId'],'ogk_'+u['KomaResourceId']]:unit_links.setdefault(n,[]).append(dict(id=str(u['Id']),name=langs.get('m_unit',{}).get(str(u['NameLanguageId']),'')))
    image_added=[dict(portraits[n],units=unit_links.get(n,[]),cached=cache_path(current,portraits[n]).is_file()) for n in sorted(portraits.keys()-old_portraits.keys())]
    image_changed=[dict(before=old_portraits[n],after=portraits[n]) for n in sorted(portraits.keys()&old_portraits.keys()) if portraits[n]['md5']!=old_portraits[n]['md5']]
    write(out/'image_index_comparison.json',dict(baseline_images=len(old_portraits),current_images=len(portraits),added=image_added,changed=image_changed,removed=sorted(old_portraits.keys()-portraits.keys()),added_unmapped=sum(not r['units'] for r in image_added)))
    summary=dict(raw_export=str(current),baseline_export=str(old),raw_files=len(b),raw_bytes=sum(r['bytes'] for r in b.values()),new_index_resources=len(delta['added']),changed_index_resources=len(delta['changed']),removed_index_resources=len(delta['removed']),new_index_cached=sum(r['cached'] for r in selected if r['index_change']=='added'),changed_index_cached=sum(r['cached'] for r in selected if r['index_change']=='changed'),new_mech_rows=sum(r['table']=='m_unit' for r in named),new_character_rows=sum(r['table']=='m_character' for r in named),new_supporter_rows=sum(r['table']=='m_supporter' for r in named),new_mech_image_resources=len(image_added),unmapped_new_mech_images=sum(not r['units'] for r in image_added),new_or_changed_cache_files=len(octo),cache_delta_bytes=sum(r['size'] for r in octo),index_database_revision=new_db['revision'],apk_changed=not file_summary['apk_identical'],master=master_summary)
    write(out/'summary.json',summary);print('COMPLETE',json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
