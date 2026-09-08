#!/usr/bin/env python3
"""Translate a TransPCB constraint spec into KiCad rules and router flags.

    apply_constraints.py SPEC.yaml --project BOARD.kicad_pro   # write rules
    apply_constraints.py SPEC.yaml --router-flags               # print CLI flags
    apply_constraints.py SPEC.yaml --lock rules.lock.json       # snapshot rules

The router rewrites .kicad_pro design rules to match whatever it produced
(see docs/gotchas.md). Re-run --project after every routing pass, and use
--lock / check_rules.py to detect it.
"""
from __future__ import annotations
import argparse, json, sys, pathlib

try:
    import yaml
except ImportError:
    sys.exit("pyyaml missing - run setup/install.sh")


def load(p):
    return yaml.safe_load(open(p))


def ipc2221_width_mm(current_a: float, temp_rise_c: float, copper_oz: float,
                     external: bool = True) -> float:
    """Minimum track width for a current, per IPC-2221.

        A_mils2 = (I / (k * dT^b)) ** (1/c)
        width   = A / (thickness_in_mils)

    k = 0.048 external, 0.024 internal (internal traces cool worse).
    """
    k = 0.048 if external else 0.024
    b, c = 0.44, 0.725
    area_mils2 = (current_a / (k * (temp_rise_c ** b))) ** (1 / c)
    thickness_mils = copper_oz * 1.378
    return round(area_mils2 / thickness_mils * 0.0254, 3)


def resolve_widths(spec: dict) -> dict:
    """Net class -> track width, deriving from current where given."""
    st = spec.get('stackup', {})
    oz = st.get('copper_oz', 1.0)
    out = {}
    for name, nc in (spec.get('net_classes') or {}).items():
        w = nc.get('track_mm')
        if nc.get('current_a'):
            dt = nc.get('max_temp_rise_c', 10)
            need = ipc2221_width_mm(nc['current_a'], dt, oz)
            if w is None or need > w:
                out[name] = {'width': need, 'why': f"IPC-2221: {nc['current_a']}A at dT={dt}C on {oz}oz"}
                continue
            out[name] = {'width': w, 'why': f"declared (IPC-2221 needs only {need} mm)"}
        elif w:
            out[name] = {'width': w, 'why': 'declared'}
    return out


def validate(spec: dict) -> list[str]:
    """Fab floors are hard limits; anything below them is unmanufacturable."""
    errs = []
    fab, d = spec['fab'], spec['defaults']
    if d['track_mm'] < fab['min_track_mm']:
        errs.append(f"defaults.track_mm {d['track_mm']} < fab floor {fab['min_track_mm']}")
    if d['clearance_mm'] < fab['min_clearance_mm']:
        errs.append(f"defaults.clearance_mm {d['clearance_mm']} < fab floor {fab['min_clearance_mm']}")
    if d['via']['drill_mm'] < fab['min_drill_mm']:
        errs.append(f"via drill {d['via']['drill_mm']} < fab floor {fab['min_drill_mm']}")
    ann = (d['via']['size_mm'] - d['via']['drill_mm']) / 2
    if ann < fab['min_annular_mm']:
        errs.append(f"via annular ring {ann:.3f} < fab floor {fab['min_annular_mm']}")
    for nc, body in spec.get('net_classes', {}).items():
        if body.get('track_mm', 1) < fab['min_track_mm']:
            errs.append(f"net_class {nc} track {body['track_mm']} < fab floor")
    for dp in spec.get('diff_pairs', []):
        if dp['gap_mm'] < fab['min_clearance_mm']:
            errs.append(f"diff pair {dp['name']} gap {dp['gap_mm']} < clearance floor")
        # Impedance without a stackup silently uses KiCad defaults - refuse it.
        if dp.get('impedance_ohm') and not spec.get('stackup'):
            errs.append(f"diff pair {dp['name']} sets impedance_ohm={dp['impedance_ohm']} but no "
                        f"stackup: block is defined. Width would be derived from KiCad defaults, "
                        f"not your fab's stack. Add stackup: or drop impedance_ohm.")
    # Current-derived widths must still clear the fab floor.
    for name, r in resolve_widths(spec).items():
        if r['width'] < fab['min_track_mm']:
            errs.append(f"net_class {name} needs {r['width']} mm ({r['why']}) "
                        f"but fab floor is {fab['min_track_mm']} mm")
    return errs


def write_project(spec: dict, pro_path: pathlib.Path):
    pro = json.load(open(pro_path))
    fab, d = spec['fab'], spec['defaults']
    rules = pro['board']['design_settings']['rules']
    rules.update({
        'min_clearance': fab['min_clearance_mm'],
        'min_track_width': fab['min_track_mm'],
        'min_through_hole_diameter': fab['min_drill_mm'],
        'min_annular_width': fab['min_annular_mm'],
        'min_hole_to_hole': fab['min_hole_to_hole_mm'],
        'min_copper_edge_clearance': fab['min_edge_clearance_mm'],
        'min_text_height': fab['min_silk_height_mm'],
        'min_via_diameter': round(d['via']['drill_mm'] + 2 * fab['min_annular_mm'], 3),
    })
    for c in pro['net_settings']['classes']:
        if c.get('name') == 'Default':
            c.update({'clearance': d['clearance_mm'], 'track_width': d['track_mm'],
                      'via_diameter': d['via']['size_mm'], 'via_drill': d['via']['drill_mm']})
    json.dump(pro, open(pro_path, 'w'), indent=2)
    return rules


def router_flags(spec: dict, stage: str = 'single') -> list[str]:
    """Build autorouter CLI flags. stage: 'single' | 'diff'."""
    d, fab, b = spec['defaults'], spec['fab'], spec.get('budgets', {})
    f = ['--track-width', str(d['track_mm']),
         '--clearance', str(d['clearance_mm']),
         '--via-size', str(d['via']['size_mm']),
         '--via-drill', str(d['via']['drill_mm']),
         '--layers'] + spec['board']['layers']
    if b.get('heuristic_weight'):
        f += ['--heuristic-weight', str(b['heuristic_weight'])]
    # Via budget -> cost weight. Default 75 = 5mm of path; tighten it when the
    # caller wants fewer layer changes.
    if b.get('max_vias'):
        f += ['--via-cost', str(int(75 * max(1.0, 120 / max(b['max_vias'], 1))))]
    if b.get('spread_tracks'):
        f += ['--track-proximity-cost', '5.0']
    if stage == 'diff':
        dp = spec['diff_pairs'][0]
        f += ['--diff-pair-gap', str(dp['gap_mm'])]
        if dp.get('impedance_ohm'):
            f += ['--impedance', str(dp['impedance_ohm'])]
    else:
        widths = resolve_widths(spec)
        power, pw = [], []
        for name, nc in (spec.get('net_classes') or {}).items():
            w = widths.get(name, {}).get('width', 0)
            if w > d['track_mm']:
                for n in nc['nets']:
                    power.append(n); pw.append(str(w))
        if power:
            f += ['--power-nets'] + power + ['--power-nets-widths'] + pw
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('spec')
    ap.add_argument('--project', help='.kicad_pro to write rules into')
    ap.add_argument('--router-flags', action='store_true')
    ap.add_argument('--stage', default='single', choices=['single', 'diff'])
    ap.add_argument('--lock', help='write a rules snapshot for drift detection')
    ap.add_argument('--widths', action='store_true', help='show resolved net-class widths')
    a = ap.parse_args()
    spec = load(a.spec)

    errs = validate(spec)
    if errs:
        print('CONSTRAINT SPEC INVALID:', file=sys.stderr)
        for e in errs:
            print('  -', e, file=sys.stderr)
        sys.exit(2)

    if a.project:
        r = write_project(spec, pathlib.Path(a.project))
        print(f'wrote rules to {a.project}')
        for k in sorted(r):
            if k.startswith('min_'):
                print(f'  {k:32} {r[k]}')
    if a.lock:
        pro = json.load(open(a.project or a.lock.replace('.lock.json', '.kicad_pro')))
        json.dump(pro['board']['design_settings']['rules'], open(a.lock, 'w'), indent=2, sort_keys=True)
        print(f'locked rules snapshot -> {a.lock}')
    if a.widths:
        for name, r in resolve_widths(spec).items():
            print(f'  {name:10} {r["width"]:.3f} mm   {r["why"]}')
    if a.router_flags:
        print(' '.join(router_flags(spec, a.stage)))


if __name__ == '__main__':
    main()
