"""Generate two multi-resolution Windows ICOs without external dependencies."""
import argparse
import math
import struct
import zlib
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 128, 256)
MINT = (94, 234, 184, 255)
BLUE = (147, 197, 253, 255)


def segment(x, y, a, b, width):
    dx, dy = b[0]-a[0], b[1]-a[1]
    t = max(0, min(1, ((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy)))
    return (x-a[0]-t*dx)**2+(y-a[1]-t*dy)**2 <= (width/2)**2


def rounded(x, y, inset, radius):
    d = max(inset+radius-x, 0, x-(64-inset-radius))
    e = max(inset+radius-y, 0, y-(64-inset-radius))
    return d*d+e*e <= radius*radius


def pixel(x, y, update):
    if not rounded(x, y, 2, 14):
        return (0, 0, 0, 0)
    bg = (25, 43, 64, 255) if update else (18, 43, 38, 255)
    border = (48, 79, 110, 255) if update else (39, 90, 75, 255)
    if not rounded(x, y, 3.4, 12.6):
        return border
    if update:
        angle = math.degrees(math.atan2(y-32, x-32)) % 360
        if 15.0 <= math.hypot(x-32, y-32) <= 20.0 and (angle<=135 or 180<=angle<=315):
            return BLUE
        arrows = [((50, 18),(50, 31)),((50,31),(38,31)),
                  ((14,46),(14,33)),((14,33),(26,33))]
        if any(segment(x,y,a,b,4.8) for a,b in arrows):
            return BLUE
    else:
        if any(segment(x,y,a,b,2.3) for a,b in [((13,15),(13,49)),((13,49),(51,49))]):
            return (66, 114, 101, 255)
        path = [(16,42),(26,31),(35,36),(49,20)]
        if any(segment(x,y,a,b,5.0) for a,b in zip(path,path[1:])):
            return MINT
        if any(segment(x,y,a,b,4.6) for a,b in [((39,20),(49,20)),((49,20),(49,30))]):
            return MINT
    return bg


def chunk(kind, data):
    return struct.pack(">I", len(data))+kind+data+struct.pack(">I", zlib.crc32(kind+data)&0xffffffff)


def png(size, update):
    # Four subpixel samples, with alpha-weighted RGB at transparent edges.
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            samples = [pixel((x+dx)*64/size,(y+dy)*64/size,update)
                       for dy in (.25,.75) for dx in (.25,.75)]
            alpha = sum(p[3] for p in samples)
            rows.extend([round(sum(p[c]*p[3] for p in samples)/alpha) if alpha else 0
                         for c in range(3)]+[round(alpha/4)])
    header = struct.pack(">IIBBBBB",size,size,8,6,0,0,0)
    return b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",header)+chunk(b"IDAT",zlib.compress(rows,9))+chunk(b"IEND",b"")


def icon(update):
    images = [png(s,update) for s in SIZES]
    offset = 6+16*len(images)
    entries = bytearray()
    for size,data in zip(SIZES,images):
        entries.extend(struct.pack("<BBBBHHII",size%256,size%256,0,0,1,32,len(data),offset))
        offset += len(data)
    return struct.pack("<HHH",0,1,len(images))+entries+b"".join(images)


def main():
    p=argparse.ArgumentParser()
    p.add_argument("output",type=Path)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    for name,update in (("ccs-terminal-v2.ico",False),("ccs-update-v2.ico",True)):
        path=args.output/name
        path.write_bytes(icon(update))


if __name__=="__main__":
    main()
