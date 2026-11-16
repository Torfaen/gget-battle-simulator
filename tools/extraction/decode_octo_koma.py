"""Offline decoder reconstructed from the client's XOR stream implementation."""

def make_mask(key):
    codes=list(key.encode('utf-16-le'))[::2]
    if not codes:raise ValueError('Empty key')
    mask=bytearray(len(codes)*2)
    for i,c in enumerate(codes):mask[i*2]=mask[len(mask)-1-i*2]=c
    h=0xab
    for c in mask:h=((h>>1)|((h&1)<<7))^c
    return bytes(c^h for c in mask)

def decode(data,key,limit=None):
    mask=make_mask(key)
    out=bytearray(data)
    out[:5]=b'Unity'
    for i in range(5,min(len(out),limit or len(out))):out[i]^=mask[i%len(mask)]
    return bytes(out)

def decode_item(data,item):
    if data.startswith(b'UnityFS'):return data,{'encoding':'UnityFS'}
    keys=[item['name'],item['md5'],str(item['id']), 'A'+str(item['id']),
          bytes.fromhex(item['object_name_hex']).decode('ascii')]
    for key in keys:
        head=decode(data[:128],key)
        if head[:8]!=b'UnityFS\0':continue
        if not 6<=int.from_bytes(head[8:12],'big')<=9:continue
        # Caller validates the complete bundle with UnityPy before exporting.
        return decode(data,key),{'encoding':'client-xor-v1','key_source':'runtime-item-field'}
    raise ValueError('No validated header from runtime item fields')

