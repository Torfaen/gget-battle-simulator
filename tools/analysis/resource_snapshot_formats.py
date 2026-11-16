# Previously verified MessagePack and encrypted Octo database readers.

import struct

import hashlib

from collections import Counter

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

class MessagePack:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def take(self, size):
        if self.offset + size > len(self.data):
            raise ValueError('Truncated MessagePack')
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def number(self, fmt):
        return struct.unpack('>' + fmt, self.take(struct.calcsize(fmt)))[0]

    def read(self):
        code = self.take(1)[0]
        if code < 0x80:
            return code
        if code >= 0xe0:
            return code - 256
        if 0xa0 <= code <= 0xbf:
            return self.take(code & 31).decode('utf-8')
        if 0x90 <= code <= 0x9f:
            return [self.read() for _ in range(code & 15)]
        if 0x80 <= code <= 0x8f:
            return {self.read(): self.read() for _ in range(code & 15)}
        if code in (0xc0, 0xc2, 0xc3):
            return {0xc0: None, 0xc2: False, 0xc3: True}[code]
        formats = {0xca: 'f', 0xcb: 'd', 0xcc: 'B', 0xcd: 'H', 0xce: 'I', 0xcf: 'Q',
                   0xd0: 'b', 0xd1: 'h', 0xd2: 'i', 0xd3: 'q'}
        if code in formats:
            return self.number(formats[code])
        if code in (0xd9, 0xda, 0xdb):
            return self.take(self.number({0xd9: 'B', 0xda: 'H', 0xdb: 'I'}[code])).decode('utf-8')
        if code in (0xc4, 0xc5, 0xc6):
            return self.take(self.number({0xc4: 'B', 0xc5: 'H', 0xc6: 'I'}[code]))
        if code in (0xdc, 0xdd):
            return [self.read() for _ in range(self.number('H' if code == 0xdc else 'I'))]
        if code in (0xde, 0xdf):
            return {self.read(): self.read() for _ in range(self.number('H' if code == 0xde else 'I'))}
        raise ValueError(f'Unsupported MessagePack code {code:#x} at {self.offset - 1}')

def varint(data,offset):
    value=0
    for shift in range(0,70,7):
        b=data[offset];offset+=1;value|=(b&127)<<shift
        if not b&128:return value,offset
    raise ValueError('Invalid varint')

def protobuf(data):
    offset=0; fields={}
    while offset<len(data):
        key,offset=varint(data,offset);field=key>>3;wire=key&7
        assert field>0
        if wire==0:value,offset=varint(data,offset)
        elif wire==2:
            size,offset=varint(data,offset);value=data[offset:offset+size];offset+=size;assert len(value)==size
        elif wire in [1,5]:
            size=8 if wire==1 else 4;value=data[offset:offset+size];offset+=size;assert len(value)==size
        else:raise ValueError('Unsupported wire '+str(wire))
        fields.setdefault(field,[]).append(value)
    assert offset==len(data)
    return fields

def decode_octo_database(path,key):
    data=path.read_bytes();assert data[0]==1 and (len(data)-17)%16==0
    d=Cipher(algorithms.AES(key),modes.CBC(data[1:17])).decryptor();plain=d.update(data[17:])+d.finalize();pad=plain[-1]
    assert 1<=pad<=16 and plain[-pad:]==bytes([pad])*pad
    db=protobuf(plain[:-pad]);items={}
    for group in [2,4]:
        for record in db.get(group,[]):
            f=protobuf(record)
            item=dict(id=f[1][0],name=f[3][0].decode(),size=f[4][0],md5=f[10][0].decode(),object_name_hex=f[11][0].hex(),database_group='assetBundleList' if group==2 else 'resourceList')
            assert len(item['md5'])==32 and item['size']>=0
            assert item['name'] not in items, item['name']
            items[item['name']]=item
    return dict(revision=db[1][0],source_sha256=hashlib.sha256(data).hexdigest(),items=items,groups=dict(Counter(r['database_group'] for r in items.values())))
