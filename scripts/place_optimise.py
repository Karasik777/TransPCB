#!/usr/bin/env python3
"""Placement refinement by simulated annealing.

    place_optimise.py BOARD.kicad_pcb --sch BOARD.kicad_sch --spec constraints.yaml
                      [--out OUT.kicad_pcb] [--iters 20000] [--seed 0] [--dry-run]

Starts from your placement and nudges movable parts to reduce a weighted cost.
Anchors (connectors, modules, mounting holes) never move. It refines - it does
not place from scratch - so the result stays recognisable and cannot wreck a
layout you are happy with.

Cost terms and their weights live in constraints.yaml under `placement_cost`,
so priorities are tunable per board without touching this file.

Annealing, not gradient descent: the cost surface is full of local minima
(swapping two caps is a discrete jump), and accepting occasional uphill moves
is what escapes them.
"""
from __future__ import annotations
import argparse, json, math, random, re, subprocess, sys, tempfile, pathlib
from collections import defaultdict
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import kicad_sexp as K
import place_rules as PR

try:
    import yaml
except ImportError:
    sys.exit('pyyaml missing - run setup/install.sh')

DEFAULT_WEIGHTS = {
    'ratsnest_mm': 1.0, 'decap_violation': 50.0, 'overlap': 500.0,
    'keepout': 500.0, 'connector_edge': 20.0, 'area_mm2': 0.5, 'off_board': 1000.0,
}
DEFAULT_ANCHORS = ['U*', 'J*', 'H*', 'MH*']


def glob_match(ref, patterns):
    import fnmatch
    return any(fnmatch.fnmatch(ref, p) for p in patterns)


class Layout:
    """Mutable part positions plus everything needed to score them."""

    def __init__(self, board_text, sch_path, spec):
        self.spec = spec or {}
        self.band = self.spec.get('placement_band', 'hf')
        self.lim = PR.BANDS[self.band]
        self.w = {**DEFAULT_WEIGHTS, **(self.spec.get('placement_cost') or {})}
        pl = self.spec.get('placement') or {}
        self.anchors = pl.get('anchors', DEFAULT_ANCHORS)
        self.radius = pl.get('move_radius_mm', 8.0)
        self.grid = pl.get('grid_mm', 0.25)

        self.fps = {f['ref']: f for f in K.footprints(board_text)}
        self.pin2net, self.net2pins = PR.schematic_nets(sch_path)
        self.vals = PR.part_values(open(sch_path).read())
        self.outline = PR.board_outline(board_text) or (0, 0, 100, 100)

        # local pad offsets so a part can be moved without re-parsing
        self.local = {}
        self.pos = {}
        self.extent = {}
        for ref, f in self.fps.items():
            cx = sum(p['x'] for p in f['pads']) / len(f['pads']) if f['pads'] else f['x']
            cy = sum(p['y'] for p in f['pads']) / len(f['pads']) if f['pads'] else f['y']
            self.pos[ref] = (cx, cy)
            self.local[ref] = [(p['num'], p['x'] - cx, p['y'] - cy, p['r'],
                                self.pin2net.get((ref, p['num']))) for p in f['pads']]
            self.extent[ref] = self._courtyard(board_text, ref, cx, cy, f)
        self.home = dict(self.pos)
        self.movable = [r for r in self.fps if not glob_match(r, self.anchors)]
        self.ics = [r for r in self.fps if r.startswith('U')]

        # precompute: which IC pin does each decoupling cap serve?
        self.decaps = []
        for ref in self.fps:
            if not ref.startswith('C'):
                continue
            nets = {n for _, _, _, _, n in self.local[ref]}
            rails = [n for n in nets if n and any(h in n.upper() for h in PR.POWER_HINTS)]
            if not (rails and 'GND' in nets):
                continue
            F = PR.cap_farads(self.vals.get(ref, ''))
            targets = [(r, p) for r, p in self.net2pins.get(rails[0], []) if r in self.ics]
            if targets:
                self.decaps.append({'ref': ref, 'targets': targets,
                                    'limit': self.lim['bulk'] if (F and F >= 1e-6) else self.lim['decap']})

    @staticmethod
    def _courtyard(board_text, ref, cx, cy, f):
        """Real F.CrtYd extent relative to the pad centroid.

        A pad bounding box is not a courtyard: a tactile switch measured
        7.8 x 5.5 mm of courtyard around 5.2 x 1.7 mm of pads. Using the proxy
        made the optimiser report zero overlap while DRC found five.
        """
        blk = board_text[f['start']:f['end'] + 1]
        at = re.search(r'\(at ([\d.-]+) ([\d.-]+)(?: ([\d.-]+))?\)', blk)
        fx, fy = float(at.group(1)), float(at.group(2))
        rot = math.radians(float(at.group(3) or 0))
        xs, ys = [], []
        def add(lx, ly):
            gx = fx + lx * math.cos(rot) - ly * math.sin(rot)
            gy = fy + lx * math.sin(rot) + ly * math.cos(rot)
            xs.append(gx - cx); ys.append(gy - cy)

        for tag in ('fp_line', 'fp_rect', 'fp_poly', 'fp_circle', 'fp_arc'):
            for st, en, sub in K.iter_blocks(blk, tag, top_level=False):
                if 'CrtYd' not in sub:
                    continue
                if tag == 'fp_circle':
                    # A circle's extent is centre +/- radius. Reading only its two
                    # stored points collapses it to a line - which made every
                    # test-point courtyard 1.25 x 0.00 mm and invisible to the
                    # overlap check.
                    ctr = re.search(r'\(center ([\d.-]+) ([\d.-]+)\)', sub)
                    end = re.search(r'\(end ([\d.-]+) ([\d.-]+)\)', sub)
                    if ctr and end:
                        cx0, cy0 = float(ctr.group(1)), float(ctr.group(2))
                        r = math.dist((cx0, cy0), (float(end.group(1)), float(end.group(2))))
                        for ddx, ddy in ((-r, -r), (r, -r), (-r, r), (r, r)):
                            add(cx0 + ddx, cy0 + ddy)
                        continue
                for a, b in re.findall(r'\((?:start|end|center|mid|xy) ([\d.-]+) ([\d.-]+)\)', sub):
                    add(float(a), float(b))
        if xs:
            return (min(xs), max(xs), min(ys), max(ys))
        pxs = [p['x'] - cx for p in f['pads']] or [0]
        pys = [p['y'] - cy for p in f['pads']] or [0]
        r = max((p['r'] for p in f['pads']), default=0.5)
        return (min(pxs) - r - 0.4, max(pxs) + r + 0.4, min(pys) - r - 0.4, max(pys) + r + 0.4)

    def pad_xy(self, ref, pin):
        if ref not in self.pos:
            return None
        cx, cy = self.pos[ref]
        for num, dx, dy, _, _ in self.local[ref]:
            if num == pin:
                return (cx + dx, cy + dy)
        return None

    # ---------------------------------------------------------------- cost
    def cost(self, detail=False):
        w, c = self.w, defaultdict(float)

        # ratsnest: star-model airwire length per net (cheap proxy for routability)
        for net, pins in self.net2pins.items():
            if net == 'GND' or len(pins) < 2:
                continue
            pts = [xy for r, p in pins if (xy := self.pad_xy(r, p))]
            if len(pts) < 2:
                continue
            cx = sum(p[0] for p in pts) / len(pts); cy = sum(p[1] for p in pts) / len(pts)
            c['ratsnest_mm'] += sum(math.dist(p, (cx, cy)) for p in pts)

        # decoupling: penalise every mm past the band limit
        for d in self.decaps:
            here = self.pos[d['ref']]
            best = min((math.dist(here, xy) for r, p in d['targets']
                        if (xy := self.pad_xy(r, p))), default=None)
            if best and best > d['limit']:
                c['decap_violation'] += best - d['limit']

        # courtyard overlap between movable parts and everything else
        refs = list(self.pos)
        for i, a in enumerate(refs):
            ax1, ax2, ay1, ay2 = self.extent[a]
            axc, ayc = self.pos[a]
            for b in refs[i + 1:]:
                if a not in self.movable and b not in self.movable:
                    continue
                bx1, bx2, by1, by2 = self.extent[b]
                bxc, byc = self.pos[b]
                ox = min(axc + ax2, bxc + bx2) - max(axc + ax1, bxc + bx1)
                oy = min(ayc + ay2, byc + by2) - max(ayc + ay1, byc + by1)
                if ox > 0 and oy > 0:
                    c['overlap'] += ox * oy

        # keepouts and board edge
        x1, y1, x2, y2 = self.outline
        for ko in self.spec.get('keepouts') or []:
            r = ko['rect']
            for ref in self.movable:
                px, py = self.pos[ref]
                ex1, ex2, ey1, ey2 = self.extent[ref]
                ox = min(px + ex2, r['x2']) - max(px + ex1, r['x1'])
                oy = min(py + ey2, r['y2']) - max(py + ey1, r['y1'])
                if ox > 0 and oy > 0:
                    c['keepout'] += ox * oy
        for ref in self.movable:
            px, py = self.pos[ref]
            ex1, ex2, ey1, ey2 = self.extent[ref]
            out = (max(0, x1 - (px + ex1)) + max(0, (px + ex2) - x2)
                   + max(0, y1 - (py + ey1)) + max(0, (py + ey2) - y2))
            if out > 0:
                c['off_board'] += out

        # connectors want edge access (anchors usually, but score it anyway)
        for ref in self.pos:
            if ref.startswith('J'):
                px, py = self.pos[ref]
                edge = min(px - x1, x2 - px, py - y1, y2 - py)
                if edge > 8.0:
                    c['connector_edge'] += edge - 8.0

        # sprawl: bounding box of all parts
        xs = [self.pos[r][0] for r in self.pos]; ys = [self.pos[r][1] for r in self.pos]
        c['area_mm2'] += (max(xs) - min(xs)) * (max(ys) - min(ys))

        total = sum(w.get(k, 0) * v for k, v in c.items())
        return (total, dict(c)) if detail else total

    # Legality is not tradeable. A weighted overlap term is always worth buying
    # if the ratsnest gain is bigger - which it usually is - so these are checked
    # separately and any move creating one is rejected outright.
    HARD = ('overlap', 'keepout', 'off_board')

    def illegal(self) -> float:
        _, d = self.cost(detail=True)
        return sum(d.get(k, 0) for k in self.HARD)

    # ---------------------------------------------------------------- search
    def anneal(self, iters=20000, seed=0, t0=None, verbose=True):
        rnd = random.Random(seed)
        if not self.movable:
            return self.cost(), 0
        cur = self.cost()
        start = cur
        base_illegal = self.illegal()   # tolerate pre-existing violations, never add
        t0 = t0 if t0 is not None else max(cur * 0.02, 1.0)
        best, best_pos = cur, dict(self.pos)
        accepted = 0
        for i in range(iters):
            T = t0 * (1 - i / iters) ** 2 + 1e-6
            ref = rnd.choice(self.movable)
            old = self.pos[ref]
            if rnd.random() < 0.15 and len(self.movable) > 1:
                # swap two same-prefix parts: escapes decap-ordering minima
                cands = [r for r in self.movable if r[0] == ref[0] and r != ref]
                if not cands:
                    continue
                other = rnd.choice(cands)
                o2 = self.pos[other]
                self.pos[ref], self.pos[other] = o2, old
                new = self.cost()
                if self.illegal() > base_illegal + 1e-9:
                    self.pos[ref], self.pos[other] = old, o2
                    continue
                if new < cur or rnd.random() < math.exp(-(new - cur) / T):
                    cur = new; accepted += 1
                    if new < best:
                        best, best_pos = new, dict(self.pos)
                else:
                    self.pos[ref], self.pos[other] = old, o2
                continue
            step = max(self.grid, self.radius * (1 - i / iters) * 0.5)
            hx, hy = self.home[ref]
            nx = old[0] + rnd.uniform(-step, step)
            ny = old[1] + rnd.uniform(-step, step)
            # stay within the allowed radius of where the user put it
            if math.dist((nx, ny), (hx, hy)) > self.radius:
                continue
            g = self.grid
            self.pos[ref] = (round(nx / g) * g, round(ny / g) * g)
            new = self.cost()
            if self.illegal() > base_illegal + 1e-9:
                self.pos[ref] = old
                continue
            if new < cur or rnd.random() < math.exp(-(new - cur) / T):
                cur = new; accepted += 1
                if new < best:
                    best, best_pos = new, dict(self.pos)
            else:
                self.pos[ref] = old
            if verbose and iters >= 1000 and i % (iters // 10) == 0:
                print(f'  {i:>7}/{iters}  T={T:8.2f}  cost={cur:10.1f}  best={best:10.1f}')
        self.pos = best_pos
        return start, best

    def moved(self, tol=0.05):
        return {r: (self.home[r], self.pos[r]) for r in self.movable
                if math.dist(self.home[r], self.pos[r]) > tol}


def write_board(board_text, layout, out_path):
    """Rewrite each moved footprint's (at ...) by its centre delta."""
    text = board_text
    for ref, (old, new) in sorted(layout.moved().items(), key=lambda kv: -len(kv[0])):
        dx, dy = new[0] - old[0], new[1] - old[1]
        i = text.index(f'"Reference" "{ref}"')
        st = text.rfind('(footprint', 0, i)
        en = K.block_end(text, st)
        blk = text[st:en + 1]
        m = re.search(r'\(at ([\d.-]+) ([\d.-]+)((?: [\d.-]+)?)\)', blk)
        nx, ny = float(m.group(1)) + dx, float(m.group(2)) + dy
        blk2 = blk[:m.start()] + f'(at {nx:.4f} {ny:.4f}{m.group(3)})' + blk[m.end():]
        text = text[:st] + blk2 + text[en + 1:]
    open(out_path, 'w').write(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('board'); ap.add_argument('--sch', required=True)
    ap.add_argument('--spec'); ap.add_argument('--out')
    ap.add_argument('--iters', type=int, default=20000)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--restarts', type=int, default=1)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--strip-tracks', action='store_true',
                    help='remove existing tracks/vias from the output (they will not follow the parts)')
    ap.add_argument('--json')
    a = ap.parse_args()

    text = open(a.board).read()
    spec = yaml.safe_load(open(a.spec)) if a.spec else {}

    # Moving parts under existing copper leaves every track pointing at where the
    # pad used to be. That is not a subtle degradation - it is hundreds of shorts.
    n_tracks = len(list(K.tracks(text)))
    if n_tracks and not a.dry_run:
        if a.strip_tracks:
            text, nt = K.drop_blocks(text, 'segment')
            text, nv = K.drop_blocks(text, 'via')
            print(f'stripped {nt} tracks and {nv} vias - re-route after optimising\n')
        else:
            print(f'REFUSING: this board has {n_tracks} tracks. Moving parts under them '
                  f'produces shorts, not a degraded route.\n'
                  f'  Optimise BEFORE routing, or pass --strip-tracks to clear them.\n'
                  f'  (--dry-run scores without writing anything.)', file=sys.stderr)
            sys.exit(2)

    base = Layout(text, a.sch, spec)
    c0, d0 = base.cost(detail=True)
    print(f'movable {len(base.movable)} parts, anchored {len(base.fps)-len(base.movable)}'
          f'   (anchors: {", ".join(base.anchors)})')
    print(f'\nstart cost {c0:.1f}')
    for k, v in sorted(d0.items()):
        print(f'  {k:18} {v:10.2f} x{base.w.get(k,0):<7} = {v*base.w.get(k,0):10.1f}')

    best_layout, best_cost = None, None
    for r in range(a.restarts):
        L = Layout(text, a.sch, spec)
        print(f'\nannealing (seed {a.seed + r}, {a.iters} iters)')
        _, c = L.anneal(iters=a.iters, seed=a.seed + r)
        if best_cost is None or c < best_cost:
            best_cost, best_layout = c, L

    c1, d1 = best_layout.cost(detail=True)
    print(f"\nfinal cost {c1:.1f}   ({100*(c1-c0)/c0:+.1f}% vs start)")
    for k in sorted(set(d0) | set(d1)):
        b, aft = d0.get(k, 0), d1.get(k, 0)
        flag = '' if abs(aft - b) < 1e-6 else ('  better' if aft < b else '  worse')
        print(f'  {k:18} {b:10.2f} -> {aft:10.2f}{flag}')

    mv = best_layout.moved()
    print(f'\n{len(mv)} part(s) moved:')
    for ref, (o, n) in sorted(mv.items()):
        print(f'  {ref:5} ({o[0]:7.2f}, {o[1]:7.2f}) -> ({n[0]:7.2f}, {n[1]:7.2f})'
              f'   {math.dist(o,n):5.2f} mm')

    if a.json:
        json.dump({'cost_before': c0, 'cost_after': c1, 'terms_before': d0,
                   'terms_after': d1, 'moved': {k: [list(v[0]), list(v[1])] for k, v in mv.items()}},
                  open(a.json, 'w'), indent=2)
    if a.out and not a.dry_run:
        write_board(text, best_layout, a.out)
        print(f'\nwrote {a.out}')
        print('Now: fill_zones.py, then DRC - the optimiser scores geometry, it does not route.')
    elif a.dry_run:
        print('\n(dry run - no board written)')


if __name__ == '__main__':
    main()
