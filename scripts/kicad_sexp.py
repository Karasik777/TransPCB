"""Balanced-paren reader/writer for KiCad s-expression files.

KiCad .kicad_pcb / .kicad_sch files are s-expressions, not line-oriented, so
grep/sed cannot safely edit them. Everything in TransPCB that touches a board
file goes through here.
"""
from __future__ import annotations
import math, re
from collections import defaultdict


def block_end(text: str, start: int) -> int:
    """Index of the ')' closing the '(' at `start`."""
    depth = 0
    i = start
    while True:
        c = text[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1


def iter_blocks(text: str, tag: str, top_level: bool = True):
    """Yield (start, end, body) for every `(tag ...)` block.

    top_level restricts to blocks at file indent level 1 (a single tab), which
    is how KiCad writes board-level segments, vias and zones - footprint-internal
    pads use deeper indentation.
    """
    pat = r'\n\t\(' + re.escape(tag) + r'\b' if top_level else r'\(' + re.escape(tag) + r'\b'
    for m in re.finditer(pat, text):
        st = m.start() + 1 if top_level else m.start()
        en = block_end(text, st)
        yield st, en, text[st:en + 1]


def drop_blocks(text: str, tag: str) -> tuple[str, int]:
    """Remove every top-level `(tag ...)` block. Returns (text, count)."""
    n = 0
    while True:
        m = re.search(r'\n\t\(' + re.escape(tag) + r'\b', text)
        if not m:
            return text, n
        st = m.start() + 1
        text = text[:st] + text[block_end(text, st) + 2:]
        n += 1


def footprints(text: str):
    """Yield dicts describing each footprint, with pads resolved to board coords."""
    for st, en, blk in iter_blocks(text, 'footprint', top_level=False):
        ref = re.search(r'\(property "Reference" "([^"]+)"', blk)
        at = re.search(r'\(at ([\d.-]+) ([\d.-]+)(?: ([\d.-]+))?\)', blk)
        if not (ref and at):
            continue
        fx, fy = float(at.group(1)), float(at.group(2))
        rot = math.radians(float(at.group(3) or 0))
        pads = []
        for pst, pen, sub in iter_blocks(blk, 'pad', top_level=False):
            num = re.search(r'\(pad "([^"]*)"', sub)
            pa = re.search(r'\(at ([\d.-]+) ([\d.-]+)', sub)
            sz = re.search(r'\(size ([\d.]+) ([\d.]+)\)', sub)
            net = re.search(r'\(net "([^"]*)"\)', sub)
            drill = re.search(r'\(drill ([\d.]+)\)', sub)
            if not (num and pa and sz):
                continue
            px, py = float(pa.group(1)), float(pa.group(2))
            pads.append({
                'num': num.group(1),
                'x': fx + px * math.cos(rot) - py * math.sin(rot),
                'y': fy + px * math.sin(rot) + py * math.cos(rot),
                'r': max(float(sz.group(1)), float(sz.group(2))) / 2,
                'net': net.group(1) if net else '',
                'drill': float(drill.group(1)) if drill else None,
            })
        yield {'ref': ref.group(1), 'x': fx, 'y': fy, 'pads': pads, 'start': st, 'end': en}


def tracks(text: str):
    """Yield every board-level track segment."""
    for st, en, sub in iter_blocks(text, 'segment'):
        a = re.search(r'\(start ([\d.-]+) ([\d.-]+)\)', sub)
        b = re.search(r'\(end ([\d.-]+) ([\d.-]+)\)', sub)
        w = re.search(r'\(width ([\d.]+)\)', sub)
        l = re.search(r'\(layer "([^"]+)"', sub)
        n = re.search(r'\(net "([^"]*)"\)', sub)
        if a and b:
            yield {'x1': float(a.group(1)), 'y1': float(a.group(2)),
                   'x2': float(b.group(1)), 'y2': float(b.group(2)),
                   'w': float(w.group(1)) if w else 0.25,
                   'layer': l.group(1) if l else '', 'net': n.group(1) if n else ''}


def vias(text: str):
    for st, en, sub in iter_blocks(text, 'via'):
        a = re.search(r'\(at ([\d.-]+) ([\d.-]+)\)', sub)
        s = re.search(r'\(size ([\d.]+)\)', sub)
        d = re.search(r'\(drill ([\d.]+)\)', sub)
        n = re.search(r'\(net "([^"]*)"\)', sub)
        if a:
            yield {'x': float(a.group(1)), 'y': float(a.group(2)),
                   'size': float(s.group(1)) if s else 0.6,
                   'drill': float(d.group(1)) if d else 0.3,
                   'net': n.group(1) if n else ''}


def zone_fills(text: str):
    """Yield (layer, [(x,y), ...]) for each filled zone polygon, skipping keepouts."""
    for st, en, zb in iter_blocks(text, 'zone'):
        if 'keepout' in zb[:400]:
            continue
        name = re.search(r'\(name "([^"]*)"\)', zb)
        for fst, fen, fb in iter_blocks(zb, 'filled_polygon', top_level=False):
            layer = 'F.Cu' if '"F.Cu"' in fb[:60] else 'B.Cu'
            pts = [(float(a), float(b)) for a, b in
                   re.findall(r'\(xy ([\d.-]+) ([\d.-]+)\)', fb)]
            if len(pts) > 2:
                yield {'layer': layer, 'pts': pts, 'name': name.group(1) if name else ''}


def net_map(text: str) -> dict:
    """{(ref, pad_number): net_name} for the whole board."""
    out = {}
    for fp in footprints(text):
        for p in fp['pads']:
            if p['num']:
                out[(fp['ref'], p['num'])] = p['net']
    return out


def make_via(x: float, y: float, net: str, size=0.6, drill=0.3, uuid_str=None) -> str:
    import uuid as _u
    u = uuid_str or str(_u.uuid4())
    return (f'\t(via\n\t\t(at {x} {y})\n\t\t(size {size})\n\t\t(drill {drill})\n'
            f'\t\t(layers "F.Cu" "B.Cu")\n\t\t(net "{net}")\n\t\t(uuid "{u}")\n\t)\n')


def append_items(text: str, body: str) -> str:
    """Insert raw s-expression items before the file's closing paren."""
    t = text.rstrip()
    assert t.endswith(')'), 'board file does not end with a closing paren'
    return t[:-1] + body + ')\n'
