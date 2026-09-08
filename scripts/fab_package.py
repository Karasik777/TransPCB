#!/usr/bin/env python3
"""Produce an orderable manufacturing package.

    fab_package.py BOARD.kicad_pcb --sch BOARD.kicad_sch --out fab/ [--vendor jlcpcb]

Gerbers + drill + BOM + pick-and-place, zipped, with the vendor quirks applied.
Refuses to run if the board has DRC errors - the point of a fab package is that
it is orderable.

JLCPCB notes baked in:
  - CPL wants columns  Designator,Mid X,Mid Y,Layer,Rotation  and mm units
  - BOM wants          Comment,Designator,Footprint,LCSC
  - gerbers: Protel extensions off, job file on, drill in Excellon mm
"""
from __future__ import annotations
import argparse, csv, json, os, pathlib, shutil, subprocess, sys, zipfile


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        print(' '.join(str(c) for c in cmd), file=sys.stderr)
        print(r.stderr[:600], file=sys.stderr)
        raise SystemExit(f'command failed: {cmd[0]} {cmd[1] if len(cmd)>1 else ""}')
    return r.stdout


def drc_gate(board, out):
    rep = out / 'drc.json'
    run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-error',
         '--output', str(rep), str(board)])
    d = json.load(open(rep))
    errs = len(d.get('violations', []))
    unc = [x for x in d.get('unconnected_items', [])
           if any('ad ' in i['description'] for i in x['items'])]
    return errs, len(unc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('board'); ap.add_argument('--sch'); ap.add_argument('--out', default='fab')
    ap.add_argument('--vendor', default='jlcpcb', choices=['jlcpcb', 'generic'])
    ap.add_argument('--layers', default='F.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts')
    ap.add_argument('--force', action='store_true', help='package despite DRC errors')
    a = ap.parse_args()

    board = pathlib.Path(a.board).resolve()
    out = pathlib.Path(a.out).resolve()
    gerb = out / 'gerbers'
    out.mkdir(parents=True, exist_ok=True); gerb.mkdir(exist_ok=True)
    name = board.stem

    print('== DRC gate')
    errs, unc = drc_gate(board, out)
    print(f'   errors {errs}, unconnected pads {unc}')
    if (errs or unc) and not a.force:
        raise SystemExit('refusing to package a board with DRC errors - fix them or pass --force')

    print('== gerbers')
    run(['kicad-cli', 'pcb', 'export', 'gerbers', '--output', str(gerb),
         '--layers', a.layers, '--no-protel-ext', '--subtract-soldermask', str(board)])
    print('== drill')
    run(['kicad-cli', 'pcb', 'export', 'drill', '--output', str(gerb),
         '--format', 'excellon', '--drill-origin', 'absolute', '--excellon-units', 'mm',
         '--generate-map', '--map-format', 'gerberx2', str(board)])

    print('== pick and place')
    pos = out / f'{name}-cpl.csv'
    run(['kicad-cli', 'pcb', 'export', 'pos', '--output', str(pos),
         '--format', 'csv', '--units', 'mm', '--side', 'both', str(board)])

    if a.vendor == 'jlcpcb':
        rows = list(csv.DictReader(open(pos)))
        with open(pos, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['Designator', 'Mid X', 'Mid Y', 'Layer', 'Rotation'])
            for r in rows:
                g = lambda *k: next((r[x] for x in k if x in r), '')
                layer = (g('Side', 'Layer') or 'top').lower()
                w.writerow([g('Ref', 'Designator'), g('PosX', 'Mid X'), g('PosY', 'Mid Y'),
                            'Top' if layer.startswith('t') else 'Bottom', g('Rot', 'Rotation')])
        print(f'   rewrote {pos.name} to JLCPCB columns ({len(rows)} parts)')
        print('   NOTE: JLC part rotation often differs from KiCad for polarised parts.')
        print('         Check diodes, electrolytics, and ICs against JLC preview before ordering.')

    if a.sch:
        print('== BOM')
        bom = out / f'{name}-bom.csv'
        run(['kicad-cli', 'sch', 'export', 'bom', '--output', str(bom),
             '--fields', 'Value,Reference,Footprint,${QUANTITY},LCSC',
             '--labels', 'Comment,Designator,Footprint,Qty,LCSC',
             '--group-by', 'Value,Footprint', str(a.sch)])
        print(f'   {bom.name}')

    zpath = out / f'{name}-{a.vendor}.zip'
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(gerb.iterdir()):
            z.write(p, p.name)
    print(f'\npackage: {zpath}')
    print(f'  gerbers+drill  {len(list(gerb.iterdir()))} files (zipped)')
    for extra in out.glob('*.csv'):
        print(f'  {extra.name}')
    print('\nUpload the zip; attach the BOM and CPL separately for assembly.')


if __name__ == '__main__':
    main()
