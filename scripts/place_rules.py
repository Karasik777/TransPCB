#!/usr/bin/env python3
"""Placement rules from standard practice, scored against a real board.

    place_rules.py BOARD.kicad_pcb --sch BOARD.kicad_sch [--spec constraints.yaml]
                   [--band lf|hf|rf] [--json report.json]

Not an auto-placer - a *critic*. It reads the netlist to learn what each part
actually does, then scores placement against rules that matter, and says which
part to move where. Feed the output back into placement and re-run.

Rules are drawn from common industry practice (Ott, Johnson & Graham, IPC-2221,
and vendor layout guides). Thresholds tighten by frequency band.
"""
from __future__ import annotations
import argparse, json, math, re, subprocess, sys, tempfile, pathlib
from collections import defaultdict
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import kicad_sexp as K

try:
    import yaml
except ImportError:
    yaml = None

# Distance limits in mm. Tighter as edge rates rise - a decoupling cap only works
# while the loop it forms with the pin is small compared to the wavelength.
BANDS = {
    'lf':  {'decap': 10.0, 'bulk': 25.0, 'xtal': 15.0, 'fb_loop': 12.0, 'label': 'low frequency / DC'},
    'hf':  {'decap': 5.0,  'bulk': 20.0, 'xtal': 10.0, 'fb_loop': 8.0,  'label': 'digital, <100 MHz edges'},
    'rf':  {'decap': 2.5,  'bulk': 15.0, 'xtal': 5.0,  'fb_loop': 5.0,  'label': 'RF / fast edges'},
}
POWER_HINTS = ('VCC', 'VDD', '+3V3', '+3.3V', '+5V', 'VBUS', 'VIN', 'VBAT', 'AVDD', 'VDDA')


def schematic_nets(sch_path):
    """{(ref,pin): net} and {net: [(ref,pin)...]} from the schematic netlist."""
    with tempfile.NamedTemporaryFile(suffix='.net', delete=False) as t:
        out = t.name
    r = subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadsexpr',
                        '--output', out, str(sch_path)], capture_output=True, text=True)
    if r.returncode != 0:
        return {}, {}
    txt = open(out).read()
    pin2net, net2pins = {}, defaultdict(list)
    for m in re.finditer(r'\(net\s+\(code "\d+"\)\s+\(name "([^"]*)"\)(.*?)(?=\(net\s+\(code|\Z)', txt, re.S):
        name = m.group(1)
        for n in re.finditer(r'\(node\s+\(ref "([^"]+)"\)\s+\(pin "([^"]+)"\)', m.group(2)):
            pin2net[(n.group(1), n.group(2))] = name
            net2pins[name].append((n.group(1), n.group(2)))
    return pin2net, dict(net2pins)


def part_values(sch_text):
    """{ref: value}, read per symbol block so properties cannot bleed across parts."""
    out = {}
    for st, en, blk in K.iter_blocks(sch_text, 'symbol', top_level=False):
        r = re.search(r'\(property "Reference" "([^"]+)"', blk)
        v = re.search(r'\(property "Value" "([^"]+)"', blk)
        if r and v and not r.group(1).startswith('#'):
            out[r.group(1)] = v.group(1)
    return out


def cap_farads(value: str):
    m = re.match(r'([\d.]+)\s*([pnum]?)F?', (value or '').strip(), re.I)
    if not m:
        return None
    mult = {'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'm': 1e-3, '': 1.0}
    try:
        return float(m.group(1)) * mult.get(m.group(2).lower(), 1.0)
    except ValueError:
        return None


def centre(fp):
    return (sum(p['x'] for p in fp['pads']) / len(fp['pads']),
            sum(p['y'] for p in fp['pads']) / len(fp['pads'])) if fp['pads'] else (fp['x'], fp['y'])


def board_outline(text):
    xs, ys = [], []
    for m in re.finditer(r'\(gr_(?:rect|line)\s*\(start ([\d.-]+) ([\d.-]+)\)\s*\(end ([\d.-]+) ([\d.-]+)\)', text):
        if 'Edge.Cuts' in text[m.start():m.start() + 400]:
            xs += [float(m.group(1)), float(m.group(3))]
            ys += [float(m.group(2)), float(m.group(4))]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def analyse(board_text, sch_path, band, spec):
    lim = BANDS[band]
    fps = {f['ref']: f for f in K.footprints(board_text)}
    pin2net, net2pins = schematic_nets(sch_path)
    sch_text = open(sch_path).read()
    vals = part_values(sch_text)
    findings = []

    def pad_xy(ref, pin):
        f = fps.get(ref)
        if not f:
            return None
        for p in f['pads']:
            if p['num'] == pin:
                return (p['x'], p['y'])
        return None

    # ---- decoupling: every cap between a power rail and GND belongs at the pin
    # of the IC it serves. Loop area, not schematic proximity, is what matters.
    ics = [r for r in fps if r.startswith('U')]
    for ref, fp in fps.items():
        if not ref.startswith('C'):
            continue
        nets = {pin2net.get((ref, p['num'])) for p in fp['pads']}
        nets.discard(None)
        rails = [n for n in nets if any(h in (n or '').upper() for h in POWER_HINTS)]
        if not (rails and 'GND' in nets):
            continue
        F = cap_farads(vals.get(ref, ''))
        is_bulk = F is not None and F >= 1e-6
        rail = rails[0]
        # which IC pins sit on this rail?
        targets = [(r, p) for r, p in net2pins.get(rail, []) if r in ics]
        if not targets:
            continue
        cxy = centre(fp)
        best = min(((math.dist(cxy, xy), r, p) for r, p in targets
                    if (xy := pad_xy(r, p))), default=None)
        if not best:
            continue
        d, tref, tpin = best
        limit = lim['bulk'] if is_bulk else lim['decap']
        kind = 'bulk' if is_bulk else 'decoupling'
        if d > limit:
            findings.append({
                'rule': f'{kind}-distance', 'ok': False, 'ref': ref,
                'msg': f'{ref} ({vals.get(ref,"?")}) is {d:.1f} mm from {tref}.{tpin} on {rail} '
                       f'- {kind} limit {limit} mm for {lim["label"]}',
                'fix': f'move {ref} to within {limit} mm of {tref} pin {tpin}'})
        else:
            findings.append({'rule': f'{kind}-distance', 'ok': True, 'ref': ref,
                             'msg': f'{ref} {d:.1f} mm from {tref}.{tpin} on {rail}'})

    # ---- smallest cap nearest the pin. A 10uF in front of a 100nF wastes the 100nF.
    by_rail_ic = defaultdict(list)
    for f in findings:
        if f['rule'].endswith('-distance') and 'from' in f['msg']:
            m = re.search(r'(\w+) \(([^)]*)\) is ([\d.]+) mm from (\w+)\.(\S+) on (\S+)', f['msg']) or \
                re.search(r'(\w+) ([\d.]+) mm from (\w+)\.(\S+) on (\S+)', f['msg'])
    caps = [(r, fps[r]) for r in fps if r.startswith('C')]
    for ic in ics:
        near = []
        for r, fp in caps:
            F = cap_farads(vals.get(r, ''))
            nets = {pin2net.get((r, p['num'])) for p in fp['pads']}
            if F is None or 'GND' not in nets:
                continue
            rails = [n for n in nets if any(h in (n or '').upper() for h in POWER_HINTS)]
            if not rails:
                continue
            targets = [pad_xy(ic, p) for rr, p in net2pins.get(rails[0], []) if rr == ic]
            targets = [t for t in targets if t]
            if not targets:
                continue
            d = min(math.dist(centre(fp), t) for t in targets)
            near.append((d, r, F))
        near.sort()
        if len(near) >= 2:
            (d0, r0, f0), (d1, r1, f1) = near[0], near[1]
            if f0 > f1 * 3 and d0 < d1:
                findings.append({
                    'rule': 'decap-ordering', 'ok': False, 'ref': r0,
                    'msg': f'{r0} ({vals.get(r0)}) sits closer to {ic} than {r1} ({vals.get(r1)}), '
                           f'but is the larger value - the small cap should be nearest the pin',
                    'fix': f'swap {r0} and {r1}'})

    # ---- connectors belong at a board edge
    out = board_outline(board_text)
    if out:
        x1, y1, x2, y2 = out
        for ref, fp in fps.items():
            if not ref.startswith('J'):
                continue
            cx, cy = centre(fp)
            edge = min(cx - x1, x2 - cx, cy - y1, y2 - cy)
            if edge > 8.0:
                findings.append({'rule': 'connector-edge', 'ok': False, 'ref': ref,
                                 'msg': f'{ref} is {edge:.1f} mm from the nearest board edge',
                                 'fix': f'move {ref} to an edge - cables need strain relief and access'})
            else:
                findings.append({'rule': 'connector-edge', 'ok': True, 'ref': ref,
                                 'msg': f'{ref} {edge:.1f} mm from edge'})

    # ---- crystals: short traces, and keep them away from switchers
    for ref, fp in fps.items():
        if not (ref.startswith('Y') or ref.startswith('X')):
            continue
        partners = set()
        for p in fp['pads']:
            n = pin2net.get((ref, p['num']))
            for r, _ in net2pins.get(n, []):
                if r != ref and r.startswith('U'):
                    partners.add(r)
        for tref in partners:
            if tref in fps:
                d = math.dist(centre(fp), centre(fps[tref]))
                ok = d <= lim['xtal']
                findings.append({'rule': 'crystal-distance', 'ok': ok, 'ref': ref,
                                 'msg': f'{ref} is {d:.1f} mm from {tref} (limit {lim["xtal"]} mm)',
                                 'fix': f'move {ref} within {lim["xtal"]} mm of {tref}' if not ok else ''})

    # ---- regulator feedback loop: keep Vout sense short
    for ref, fp in fps.items():
        if not ref.startswith('U'):
            continue
        for p in fp['pads']:
            n = pin2net.get((ref, p['num']))
            if not n:
                continue
            partners = [r for r, _ in net2pins.get(n, []) if r.startswith('C') and r in fps]
            if len(net2pins.get(n, [])) <= 6 and partners and any(
                    h in n.upper() for h in ('+3V3', '+5V', 'VOUT')):
                for c in partners:
                    d = math.dist((p['x'], p['y']), centre(fps[c]))
                    if d > lim['fb_loop']:
                        findings.append({'rule': 'regulator-output-cap', 'ok': False, 'ref': c,
                                         'msg': f'{c} is {d:.1f} mm from {ref} pin {p["num"]} on {n} '
                                                f'- regulator stability wants it within {lim["fb_loop"]} mm',
                                         'fix': f'move {c} nearer {ref} pin {p["num"]}'})
                break

    # ---- declared keepouts must be empty of parts too
    for ko in (spec or {}).get('keepouts', []) if spec else []:
        r = ko['rect']
        for ref, fp in fps.items():
            cx, cy = centre(fp)
            if r['x1'] <= cx <= r['x2'] and r['y1'] <= cy <= r['y2']:
                findings.append({'rule': f'keepout-{ko["name"]}', 'ok': False, 'ref': ref,
                                 'msg': f'{ref} sits inside keepout "{ko["name"]}" - {ko.get("reason","")}',
                                 'fix': f'move {ref} out of x {r["x1"]}..{r["x2"]}, y {r["y1"]}..{r["y2"]}'})
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('board'); ap.add_argument('--sch', required=True)
    ap.add_argument('--spec'); ap.add_argument('--band', default='hf', choices=list(BANDS))
    ap.add_argument('--json'); ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()
    spec = yaml.safe_load(open(a.spec)) if (a.spec and yaml) else None
    f = analyse(open(a.board).read(), a.sch, a.band, spec)
    print(f'placement critique - band: {a.band} ({BANDS[a.band]["label"]})\n')
    bad = [x for x in f if not x['ok']]
    for x in (f if a.verbose else bad):
        print(f"{'ok  ' if x['ok'] else 'FIX '} [{x['rule']}] {x['msg']}")
        if x.get('fix'):
            print(f"       -> {x['fix']}")
    score = round(100 * (1 - len(bad) / len(f))) if f else 100
    print(f"\n{len(f) - len(bad)}/{len(f)} rules pass   score {score}/100")
    if a.json:
        json.dump(f, open(a.json, 'w'), indent=2)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
