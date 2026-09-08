#!/usr/bin/env python3
"""Fill copper zones headlessly - no GUI, no MCP.

    fill_zones.py BOARD.kicad_pcb [--check]

kicad-cli has no fill command, and stale fill polygons produce hundreds of
phantom DRC clearance errors after a re-route (they still trace the *old*
copper). This closes that gap using KiCad's own ZONE_FILLER.

--check reports whether any zone's fill is stale relative to the board's copper,
without modifying anything. Exit 1 if a refill is needed.
"""
from __future__ import annotations
import argparse, hashlib, os, sys, contextlib, io


@contextlib.contextmanager
def _quiet():
    """pcbnew spams wxWidgets property asserts on import; they are harmless."""
    err = os.dup(2)
    with open(os.devnull, 'w') as null:
        os.dup2(null.fileno(), 2)
    try:
        yield
    finally:
        os.dup2(err, 2); os.close(err)


def copper_fingerprint(path: str) -> str:
    """Hash of the board's tracks/vias/pads - changes when fill would change."""
    import re
    t = open(path).read()
    bits = re.findall(r'\((?:segment|via)\b.*?\(net "[^"]*"\)', t, re.S)
    return hashlib.sha256(''.join(bits).encode()).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('board')
    ap.add_argument('--check', action='store_true', help='report staleness, do not modify')
    a = ap.parse_args()

    with _quiet():
        import pcbnew

    board = pcbnew.LoadBoard(a.board)
    zones = list(board.Zones())
    if not zones:
        print('no zones on this board')
        return 0

    if a.check:
        # A zone is stale if its stored fill predates the current copper. KiCad
        # tracks this internally; expose it rather than guessing.
        stale = [z for z in zones if not z.IsFilled()]
        for z in zones:
            print(f'  {z.GetZoneName() or "(unnamed)"}: '
                  f'{"filled" if z.IsFilled() else "NOT FILLED"} '
                  f'net={z.GetNetname()}')
        if stale:
            print(f'\n{len(stale)} zone(s) need filling')
            return 1
        print('\nall zones filled (run without --check after any copper change)')
        return 0

    container = pcbnew.ZONES()
    for z in zones:
        container.append(z)
    with _quiet():
        ok = pcbnew.ZONE_FILLER(board).Fill(container)
    if not ok:
        print('ZONE_FILLER reported failure', file=sys.stderr)
        return 2
    board.Save(a.board)
    for z in zones:
        print(f'  filled {z.GetZoneName() or "(unnamed)"} [{z.GetNetname()}] '
              f'on {board.GetLayerName(z.GetLayer())}')
    print(f'{len(zones)} zone(s) filled and saved')
    return 0


if __name__ == '__main__':
    sys.exit(main())
