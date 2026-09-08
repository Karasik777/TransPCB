#!/usr/bin/env python3
"""Independent board checks. These exist because the shipping tools said
"CLEAN" and "0 violations" while three real defects were live:

  - pcb_sync_from_schematic reported 100% pad coverage with four pads on
    stale nets (USB D+/D- went nowhere)
  - the F.Cu pour filled straight through the RF antenna keepout
  - the autorouter loosened .kicad_pro design rules and dropped the ground plane

Usage:
    verify.py BOARD.kicad_pcb --spec constraints.yaml [--sch BOARD.kicad_sch]
                             [--lock rules.lock.json] [--json report.json]
Exit code 1 if any check fails.
"""
from __future__ import annotations
import argparse, json, math, subprocess, sys, tempfile, pathlib, heapq
from collections import defaultdict
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import kicad_sexp as K
import physics as PH

try:
    import yaml
except ImportError:
    yaml = None


def _fail(results, name, msg, detail=None):
    results.append({'check': name, 'ok': False, 'msg': msg, 'detail': detail or []})


def _pass(results, name, msg):
    results.append({'check': name, 'ok': True, 'msg': msg})


# ---------------------------------------------------------------- net sync
def check_net_sync(board_text, sch_path, results):
    """Board pad nets must match the schematic netlist exactly."""
    with tempfile.NamedTemporaryFile(suffix='.net', delete=False) as t:
        out = t.name
    r = subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format',
                        'kicadsexpr', '--output', out, str(sch_path)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        _fail(results, 'net-sync', 'kicad-cli netlist export failed', [r.stderr[:200]])
        return
    import re
    txt = open(out).read()
    sch = {}
    for m in re.finditer(r'\(net\s+\(code "\d+"\)\s+\(name "([^"]*)"\)(.*?)(?=\(net\s+\(code|\Z)', txt, re.S):
        for n in re.finditer(r'\(node\s+\(ref "([^"]+)"\)\s+\(pin "([^"]+)"\)', m.group(2)):
            sch[(n.group(1), n.group(2))] = m.group(1)
    board = K.net_map(board_text)
    bad = [(k, board.get(k), v) for k, v in sch.items()
           if board.get(k) != v and not str(board.get(k)).startswith('unconnected-')]
    if bad:
        _fail(results, 'net-sync', f'{len(bad)} pad(s) on the wrong net',
              [f'{r}.{p}: board={b} schematic={s}' for (r, p), b, s in bad[:12]])
    else:
        _pass(results, 'net-sync', f'{len(sch)} schematic nodes match {len(board)} board pads')


# ---------------------------------------------------------------- keepouts
def check_keepouts(board_text, spec, results):
    """No copper of any kind inside a declared keepout rect."""
    kos = (spec or {}).get('keepouts') or []
    if not kos:
        return
    try:
        from shapely.geometry import Polygon, Point, box
        from shapely.ops import unary_union
    except ImportError:
        _fail(results, 'keepout', 'shapely missing - run setup/install.sh')
        return
    pours = defaultdict(list)
    for z in K.zone_fills(board_text):
        pours[z['layer']].append(Polygon(z['pts']))
    pours = {k: unary_union(v) for k, v in pours.items()}
    tr = list(K.tracks(board_text))
    vs = list(K.vias(board_text))
    for ko in kos:
        r = ko['rect']
        region = box(r['x1'], r['y1'], r['x2'], r['y2'])
        hits = []
        for layer, u in pours.items():
            if u and u.intersects(region) and u.intersection(region).area > 0.01:
                hits.append(f"{layer} pour covers {u.intersection(region).area:.2f} mm2")
        for t in tr:
            if region.intersects(Polygon([(t['x1'], t['y1']), (t['x2'], t['y2']),
                                          (t['x2'] + 1e-6, t['y2'] + 1e-6)]).buffer(t['w'] / 2)):
                hits.append(f"track [{t['net']}] on {t['layer']}")
        for v in vs:
            if region.contains(Point(v['x'], v['y'])):
                hits.append(f"via [{v['net']}] at ({v['x']}, {v['y']})")
        if hits:
            _fail(results, f"keepout:{ko['name']}", f"{len(hits)} copper item(s) inside - {ko.get('reason','')}",
                  sorted(set(hits))[:8])
        else:
            _pass(results, f"keepout:{ko['name']}", 'clear on all layers')


# ---------------------------------------------------------------- diff pairs
def _net_graph(board_text, net):
    g = defaultdict(list); pts = []
    for t in K.tracks(board_text):
        if t['net'] != net:
            continue
        a, b, l = (t['x1'], t['y1']), (t['x2'], t['y2']), t['layer']
        L = math.dist(a, b); n = max(1, int(L / 0.15)); prev = None
        for i in range(n + 1):
            u = i / n
            p = (round(a[0] + (b[0] - a[0]) * u, 3), round(a[1] + (b[1] - a[1]) * u, 3), l)
            pts.append(p)
            if prev:
                d = math.dist(prev[:2], p[:2]); g[prev].append((p, d)); g[p].append((prev, d))
            prev = p
    grid = defaultdict(list)
    for p in pts:
        grid[(int(p[0] / .16), int(p[1] / .16), p[2])].append(p)
    for items in grid.values():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if math.dist(items[i][:2], items[j][:2]) < .16:
                    g[items[i]].append((items[j], 0.)); g[items[j]].append((items[i], 0.))
    for v in K.vias(board_text):
        if v['net'] != net:
            continue
        near = [p for p in pts if math.dist(p[:2], (v['x'], v['y'])) < 0.35]
        for a in near:
            for b in near:
                if a[2] != b[2]:
                    g[a].append((b, 0.))
    return g, pts


def _path_len(board_text, net, p1, p2):
    g, pts = _net_graph(board_text, net)
    S = [p for p in pts if math.dist(p[:2], p1) < 0.9]
    E = [p for p in pts if math.dist(p[:2], p2) < 0.9]
    best = None
    for s0 in S:
        dist = {s0: 0}; pq = [(0, s0)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, 1e9):
                continue
            for v, w in g[u]:
                nd = d + w
                if nd < dist.get(v, 1e9):
                    dist[v] = nd; heapq.heappush(pq, (nd, v))
        for e in E:
            if e in dist and (best is None or dist[e] < best):
                best = dist[e]
    return best


def check_diff_pairs(board_text, spec, results):
    """Skew, via symmetry and coupling for each declared pair."""
    pairs = (spec or {}).get('diff_pairs') or []
    if not pairs:
        return
    vias_by_net = defaultdict(int)
    for v in K.vias(board_text):
        vias_by_net[v['net']] += 1
    for dp in pairs:
        p, n = dp['p'], dp['n']
        tol, why = PH.skew_budget_mm(dp.get('interface'), dp.get('rise_time_ps'),
                                     dp.get('max_skew_mm', 2.5))
        if dp.get('max_skew_mm') and not dp.get('interface'):
            tol = dp['max_skew_mm']; why = 'max_skew_mm declared explicitly'
        lp = sum(math.dist((t['x1'], t['y1']), (t['x2'], t['y2']))
                 for t in K.tracks(board_text) if t['net'] == p)
        ln = sum(math.dist((t['x1'], t['y1']), (t['x2'], t['y2']))
                 for t in K.tracks(board_text) if t['net'] == n)
        skew = abs(lp - ln)
        vp, vn = vias_by_net[p], vias_by_net[n]
        detail = [f'{p}: {lp:.2f} mm, {vp} vias', f'{n}: {ln:.2f} mm, {vn} vias', why]
        if skew > tol:
            _fail(results, f"diffpair:{dp['name']}", f'copper skew {skew:.2f} mm exceeds {tol} mm', detail)
        elif vp != vn and PH.INTERFACES.get(dp.get('interface'), {}).get('rate_mbps', 1e9) >= 100:
            _fail(results, f"diffpair:{dp['name']}",
                  f'via count asymmetric ({vp} vs {vn}) - converts differential to common mode '
                  f'at these edge rates', detail)
        elif vp != vn:
            _pass(results, f"diffpair:{dp['name']}",
                  f'skew {skew:.2f} mm within {tol} mm; vias {vp}/{vn} asymmetric but immaterial '
                  f'at this rate  [{why}]')
        else:
            _pass(results, f"diffpair:{dp['name']}",
                  f'skew {skew:.2f} mm within {tol} mm, vias {vp}/{vn}  [{why}]')


# ---------------------------------------------------------------- fab / DFM
def check_dfm(board_text, spec, results):
    fab = (spec or {}).get('fab') or {}
    if not fab:
        return
    bad = []
    for fp in K.footprints(board_text):
        for pad in fp['pads']:
            if pad['drill'] and pad['drill'] < fab['min_drill_mm'] - 1e-9:
                bad.append(f"{fp['ref']} pad {pad['num']}: drill {pad['drill']} mm")
    for v in K.vias(board_text):
        if v['drill'] < fab['min_drill_mm'] - 1e-9:
            bad.append(f"via at ({v['x']}, {v['y']}): drill {v['drill']} mm")
        ann = (v['size'] - v['drill']) / 2
        if ann < fab['min_annular_mm'] - 1e-9:
            bad.append(f"via at ({v['x']}, {v['y']}): annular {ann:.3f} mm")
    if bad:
        _fail(results, 'dfm-drill', f"{len(bad)} feature(s) below fab floor", sorted(set(bad))[:10])
    else:
        _pass(results, 'dfm-drill', f"all drills >= {fab['min_drill_mm']} mm, annular >= {fab['min_annular_mm']} mm")


# ---------------------------------------------------------------- rule drift
def check_rules(pro_path, lock_path, results):
    """The autorouter rewrites .kicad_pro rules to match what it produced."""
    if not (pro_path and pathlib.Path(pro_path).exists() and lock_path and pathlib.Path(lock_path).exists()):
        return
    cur = json.load(open(pro_path))['board']['design_settings']['rules']
    lock = json.load(open(lock_path))
    drift = [f'{k}: {lock[k]} -> {cur.get(k)}' for k in lock if cur.get(k) != lock[k]]
    if drift:
        _fail(results, 'rule-drift', f'{len(drift)} design rule(s) changed since lock', drift[:10])
    else:
        _pass(results, 'rule-drift', 'design rules match the locked snapshot')


# ---------------------------------------------------------------- planes
def check_planes(board_text, spec, results):
    """A declared plane net must actually have a filled zone on each layer."""
    for pl in (spec or {}).get('planes') or []:
        have = {z['layer'] for z in K.zone_fills(board_text)}
        missing = [l for l in pl['layers'] if l not in have]
        if missing:
            _fail(results, f"plane:{pl['net']}", f"no filled zone on {', '.join(missing)} "
                  f"(the router discards zones - see docs/gotchas.md)")
        else:
            _pass(results, f"plane:{pl['net']}", f"filled on {', '.join(pl['layers'])}")


# ---------------------------------------------------------------- thermal
def check_thermal(spec, results):
    """Dissipation vs package vs max junction temperature.

    Nothing else in the toolchain looks at heat, and it is the failure mode that
    most often makes a board that passes every geometric check stop working.
    """
    for d in (spec or {}).get('power_devices') or []:
        ref = d['ref']
        if d.get('type') == 'ldo':
            P = PH.ldo_dissipation_w(d['vin_v'], d['vout_v'], d['load_a'], d.get('iq_a', 0))
            eff = 100 * d['vout_v'] / d['vin_v']
        else:
            P = d.get('dissipation_w')
            eff = None
        if P is None:
            continue
        duty = d.get('duty_cycle', 1.0)
        if duty < 1.0:
            # A short burst does not heat the junction to its steady-state value.
            # Averaging is the right first-order model when the pulse is far
            # shorter than the package's thermal time constant.
            P_peak = P
            P = P * duty
        amb = d.get('ambient_c', 25.0)
        tj, th = PH.junction_temp(P, d.get('package', ''), amb,
                                  d.get('ground_pour', True), d.get('theta_ja'))
        if tj is None:
            _fail(results, f'thermal:{ref}', f"unknown package '{d.get('package')}' - "
                  f"add theta_ja to the spec", [f'known: {", ".join(sorted(PH.THETA_JA))}'])
            continue
        limit = d.get('max_tj_c', 125.0)
        det = [f'P = {P:.3f} W' + (f' peak {P_peak:.3f} W at {duty:.0%} duty' if duty < 1.0 else '')
               + (f' (efficiency {eff:.0f}%)' if eff else ''),
               f'theta_JA = {th} degC/W ({d.get("package")}), ambient {amb} degC',
               f'Tj = {tj:.0f} degC, limit {limit} degC']
        if tj > limit:
            _fail(results, f'thermal:{ref}',
                  f'junction {tj:.0f} degC exceeds {limit} degC at {d["load_a"]:.2f} A'
                  if d.get('load_a') else f'junction {tj:.0f} degC exceeds {limit} degC', det)
        elif tj > limit - 20:
            _fail(results, f'thermal:{ref}',
                  f'junction {tj:.0f} degC is within 20 degC of the {limit} degC limit', det)
        else:
            _pass(results, f'thermal:{ref}', f'Tj {tj:.0f} degC (limit {limit}), P = {P:.3f} W')


# ---------------------------------------------------------------- track current
def check_track_current(board_text, spec, results):
    """Routed width against the current the net actually carries."""
    fab = (spec or {}).get('fab') or {}
    oz = ((spec or {}).get('stackup') or {}).get('copper_oz', 1.0)
    want = {}
    for name, nc in ((spec or {}).get('net_classes') or {}).items():
        if not nc.get('current_a'):
            continue
        need = PH.ipc2221_width_mm(nc['current_a'], nc.get('max_temp_rise_c', 10), oz)
        for pat in nc['nets']:
            want[pat] = (need, nc['current_a'], name)
    if not want:
        return
    import fnmatch
    from collections import defaultdict
    thin = defaultdict(lambda: (1e9, None))
    for t in K.tracks(board_text):
        for pat, (need, amps, cls) in want.items():
            if fnmatch.fnmatch(t['net'], pat) and t['w'] < thin[t['net']][0]:
                thin[t['net']] = (t['w'], (need, amps, cls))
    bad = []
    for net, (w, meta) in thin.items():
        if meta and w < meta[0] - 1e-6:
            bad.append(f'{net}: narrowest {w:.2f} mm, needs {meta[0]:.3f} mm for {meta[1]} A')
    if bad:
        _fail(results, 'track-current', f'{len(bad)} net(s) narrower than IPC-2221 requires', bad[:8])
    elif thin:
        _pass(results, 'track-current',
              f'{len(thin)} current-rated net(s) meet IPC-2221 at {oz} oz')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('board')
    ap.add_argument('--spec')
    ap.add_argument('--sch')
    ap.add_argument('--project')
    ap.add_argument('--lock')
    ap.add_argument('--json')
    a = ap.parse_args()

    text = open(a.board).read()
    spec = yaml.safe_load(open(a.spec)) if (a.spec and yaml) else None
    results = []

    if a.sch:
        check_net_sync(text, a.sch, results)
    check_keepouts(text, spec, results)
    check_diff_pairs(text, spec, results)
    check_dfm(text, spec, results)
    check_track_current(text, spec, results)
    check_thermal(spec, results)
    check_planes(text, spec, results)
    check_rules(a.project, a.lock, results)

    # Considered exceptions, declared in the spec with a reason and an expiry.
    # These downgrade a FAIL to ACCEPTED - the check still runs and still prints,
    # so a regression beyond what was accepted still surfaces.
    import datetime
    accepted = {x['check']: x for x in (spec or {}).get('accepted_failures') or []}
    for r in results:
        acc = accepted.get(r['check'])
        if not (acc and not r['ok']):
            continue
        exp = acc.get('expires')
        if exp and str(exp) < datetime.date.today().isoformat():
            r['detail'] = (r.get('detail') or []) + [f"acceptance EXPIRED on {exp} - re-review"]
            continue
        r['ok'] = True
        r['accepted'] = True
        r['detail'] = (r.get('detail') or []) + [f"accepted: {acc.get('reason','no reason given')}"
                                                 + (f" (expires {exp})" if exp else '')]

    width = max((len(r['check']) for r in results), default=10)
    for r in results:
        tag = 'ACCP' if r.get('accepted') else ('PASS' if r['ok'] else 'FAIL')
        print(f"{tag}  {r['check']:<{width}}  {r['msg']}")
        for d in r.get('detail', []):
            print(f"        - {d}")
    if a.json:
        json.dump(results, open(a.json, 'w'), indent=2)
    failed = sum(1 for r in results if not r['ok'])
    print(f"\n{len(results) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
