#!/usr/bin/env python3
"""Report a footprint's real geometry BEFORE you place it.

    probe_footprint.py LIB:FOOTPRINT
    probe_footprint.py --board BOARD.kicad_pcb REF

Three placement bugs on one board all came from assuming geometry instead of
reading it: header pads run along Y with the origin at pin 1 (so rot=90 lays
them flat), a right-angle USB-C had its mating face at local +Y (so rot=180
pointed it into the board), and a tactile switch had a 7.8 x 5.5 mm courtyard
around a 3 mm body.
"""
from __future__ import annotations
import argparse, glob, math, os, re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import kicad_sexp as K

SEARCH = [os.path.expanduser('~/.local/share/kicad/*/footprints'),
          '/usr/share/kicad/footprints', '/usr/local/share/kicad/footprints']


def find_lib(spec: str) -> str | None:
    lib, fp = spec.split(':', 1)
    for root in SEARCH:
        for base in glob.glob(root):
            p = os.path.join(base, f'{lib}.pretty', f'{fp}.kicad_mod')
            if os.path.exists(p):
                return p
    return None


def _extents(text, layer_filter=None):
    xs, ys = [], []
    for tag in ('fp_line', 'fp_rect', 'fp_poly', 'fp_circle', 'fp_arc'):
        for st, en, sub in K.iter_blocks(text, tag, top_level=False):
            if layer_filter and layer_filter not in sub:
                continue
            for a, b in re.findall(r'\((?:start|end|center|mid|xy) ([\d.-]+) ([\d.-]+)\)', sub):
                xs.append(float(a)); ys.append(float(b))
    return (min(xs), max(xs), min(ys), max(ys)) if xs else None


def report(text: str, name: str):
    print(f'=== {name} ===')
    pads = []
    for st, en, sub in K.iter_blocks(text, 'pad', top_level=False):
        num = re.search(r'\(pad "([^"]*)"', sub)
        at = re.search(r'\(at ([\d.-]+) ([\d.-]+)', sub)
        sz = re.search(r'\(size ([\d.]+) ([\d.]+)\)', sub)
        dr = re.search(r'\(drill ([\d.]+)\)', sub)
        ov = re.search(r'\(drill oval ([\d.]+) ([\d.]+)\)', sub)
        typ = 'thru_hole' if 'thru_hole' in sub[:80] else ('smd' if 'smd' in sub[:80] else '?')
        if num and at:
            pads.append({'n': num.group(1), 'x': float(at.group(1)), 'y': float(at.group(2)),
                         'w': float(sz.group(1)) if sz else 0, 'h': float(sz.group(2)) if sz else 0,
                         'drill': float(dr.group(1)) if dr else (float(ov.group(1)) if ov else None),
                         'type': typ})
    if not pads:
        print('  no pads'); return
    xs = [p['x'] for p in pads]; ys = [p['y'] for p in pads]
    spanx, spany = max(xs) - min(xs), max(ys) - min(ys)
    print(f'  pads          {len(pads)}  ({sum(1 for p in pads if p["type"]=="thru_hole")} THT, '
          f'{sum(1 for p in pads if p["type"]=="smd")} SMD)')
    print(f'  pad extent    x {min(xs):+.2f}..{max(xs):+.2f}   y {min(ys):+.2f}..{max(ys):+.2f}')

    # Which way does a pin row run at rot=0?
    axis = 'Y (vertical column)' if spany > spanx * 1.5 else (
           'X (horizontal row)' if spanx > spany * 1.5 else 'both (grid/area)')
    print(f'  pad axis      {axis}   <- rot=90 turns this 90 degrees')

    # Where is the origin relative to the pads?
    p1 = next((p for p in pads if p['n'] == '1'), pads[0])
    at_origin = abs(p1['x']) < 0.01 and abs(p1['y']) < 0.01
    centred = abs((min(xs) + max(xs)) / 2) < 0.3 and abs((min(ys) + max(ys)) / 2) < 0.3
    print(f'  origin        {"pin 1" if at_origin else ("centre of pads" if centred else "offset")}'
          f'   <- placement coords refer to THIS point')

    drills = sorted({p['drill'] for p in pads if p['drill']})
    if drills:
        print(f'  drills        {", ".join(f"{d:.2f}" for d in drills)} mm'
              f'{"   <- below 0.3 fails JLC standard tier" if drills[0] < 0.3 else ""}')

    crt = _extents(text, 'F.CrtYd') or _extents(text, 'CrtYd')
    if crt:
        cw, ch = crt[1] - crt[0], crt[3] - crt[2]
        print(f'  courtyard     {cw:.2f} x {ch:.2f} mm  (x {crt[0]:+.2f}..{crt[1]:+.2f}, y {crt[2]:+.2f}..{crt[3]:+.2f})')
        if cw > spanx * 2 or ch > spany * 2:
            print('                ^ much larger than the pads - budget space around it')

    body = _extents(text, 'F.Fab') or _extents(text)
    if body:
        print(f'  body/graphics x {body[0]:+.2f}..{body[1]:+.2f}   y {body[2]:+.2f}..{body[3]:+.2f}')
        # A connector's mating face is the side the body extends well past the pads.
        over = {'+Y': body[3] - max(ys), '-Y': min(ys) - body[2],
                '+X': body[1] - max(xs), '-X': min(xs) - body[0]}
        face, amount = max(over.items(), key=lambda kv: kv[1])
        if amount > 2.0:
            print(f'  mating face   body extends {amount:.2f} mm past the pads toward {face}')
            print(f'                -> at rot=0 the opening points {face}. Put that at the board edge.')

    if 'keepout' in text:
        for st, en, zb in K.iter_blocks(text, 'zone', top_level=False):
            if 'keepout' not in zb:
                continue
            pts = [(float(a), float(b)) for a, b in re.findall(r'\(xy ([\d.-]+) ([\d.-]+)\)', zb)]
            if pts:
                kx = [p[0] for p in pts]; ky = [p[1] for p in pts]
                rules = re.findall(r'\((tracks|vias|pads|copperpour|footprints) (not_allowed|allowed)\)', zb)
                print(f'  KEEPOUT       x {min(kx):+.2f}..{max(kx):+.2f}  y {min(ky):+.2f}..{max(ky):+.2f}')
                print(f'                {", ".join(f"{a}={b}" for a, b in rules)}')
                print('                ^ declare this in constraints.yaml too - footprint keepouts')
                print('                  are NOT reliably honoured on every layer')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('target', help='LIB:FOOTPRINT, or a REF with --board')
    ap.add_argument('--board')
    a = ap.parse_args()
    if a.board:
        text = open(a.board).read()
        for st, en, blk in K.iter_blocks(text, 'footprint', top_level=False):
            ref = re.search(r'\(property "Reference" "([^"]+)"', blk)
            if ref and ref.group(1) == a.target:
                at = re.search(r'\(at ([\d.-]+) ([\d.-]+)(?: ([\d.-]+))?\)', blk)
                print(f'placed at ({at.group(1)}, {at.group(2)}) rot={at.group(3) or 0}\n')
                return report(blk, a.target)
        sys.exit(f'{a.target} not found on board')
    path = find_lib(a.target)
    if not path:
        sys.exit(f'footprint not found: {a.target}')
    report(open(path).read(), a.target)


if __name__ == '__main__':
    main()
