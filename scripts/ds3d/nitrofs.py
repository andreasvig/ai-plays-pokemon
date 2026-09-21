import struct, sys

def read_rom(path):
    d=open(path,'rb').read()
    title=d[0:12].decode('ascii','replace'); code=d[12:16].decode()
    fnt_off,fnt_size,fat_off,fat_size=struct.unpack_from('<4I',d,0x40)
    fat=[struct.unpack_from('<2I',d,fat_off+8*i) for i in range(fat_size//8)]
    # FNT
    def dirname_table(did):
        base=fnt_off
        off,first,parent=struct.unpack_from('<IHH',d,base+8*(did&0xFFF))
        return off,first,parent
    names={}
    def walk(did, prefix):
        off,first,parent=dirname_table(did)
        p=fnt_off+off; fid=first
        while True:
            t=d[p]; p+=1
            if t==0: break
            ln=t&0x7F; nm=d[p:p+ln].decode('ascii','replace'); p+=ln
            if t&0x80:
                sub=struct.unpack_from('<H',d,p)[0]; p+=2
                walk(sub, prefix+nm+'/')
            else:
                names[prefix+nm]=fid; fid+=1
    walk(0xF000,'/')
    return d, title, code, fat, names

if __name__=='__main__':
    d,title,code,fat,names=read_rom(sys.argv[1])
    print(title, code, 'files',len(fat),'named',len(names))
    pat=sys.argv[2] if len(sys.argv)>2 else ''
    hits=[k for k in sorted(names) if pat in k]
    for k in hits[:25]:
        s,e=fat[names[k]]; print(f'  {k}  id={names[k]} size={e-s} magic={d[s:s+4]}')
    print(' total matching', len(hits))

def narc_entries(buf):
    assert buf[:4]==b'NARC'
    hs=struct.unpack_from('<H',buf,12)[0]
    o=hs
    assert buf[o:o+4]==b'BTAF', buf[o:o+4]
    n=struct.unpack_from('<H',buf,o+8)[0]
    fat=[struct.unpack_from('<2I',buf,o+12+8*i) for i in range(n)]
    o+=struct.unpack_from('<I',buf,o+4)[0]
    assert buf[o:o+4]==b'BTNF'
    o+=struct.unpack_from('<I',buf,o+4)[0]
    assert buf[o:o+4]==b'GMIF', buf[o:o+4]
    base=o+8
    return [buf[base+s:base+e] for s,e in fat]
