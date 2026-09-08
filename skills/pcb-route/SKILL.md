---
name: pcb-route
description: Autoroute a KiCad board and verify the result - two-stage routing (differential pairs first, then single-ended), ground pours, stitching vias, and the post-run repairs the routers require. Use when asked to route a board, re-route nets, add ground planes or stitching, or when a routing pass needs checking.
---

# Routing a board

Two routers are available. **Default to the Rust A\*** (`KiCadRoutingTools`):
it operates on `.kicad_pcb` directly, needs no Specctra round-trip and no GUI
steps, and completes in under a second. Freerouting is the fallback - it needs
Docker, two manual GUI steps (DSN export, SES import), and on a comparison run
left one net split and two dangling stubs where the Rust router left none.

## Before touching the board

```bash
./scripts/preflight.sh BOARD.kicad_pcb --close   # aborts on the segfault state;
                                                 # --close kills a stale pcbnew
git add -A && git commit -m "pre-route checkpoint"
python scripts/apply_constraints.py constraints.yaml --project BOARD.kicad_pro --lock rules.lock.json
```

The commit is not optional. Both routers and an open pcbnew window have each
destroyed a full routing pass during development.

## Order matters

Route **differential pairs first**, then everything else:

```bash
V=.venv/bin/python
RT=~/.local/share/kicad/10.0/3rdparty/plugins/com_github_drandyhaas_kicadroutingtools/py_router

# 1. pairs - registers them as protected so stage 2 cannot rip them
KICAD_ROUTE_TRACE=1 $V $RT/route_diff.py in.kicad_pcb --output dp.kicad_pcb \
  --nets USB_DP USB_DM $(python scripts/apply_constraints.py constraints.yaml --router-flags --stage diff)

# 2. everything else, preserving stage 1
KICAD_ROUTE_TRACE=1 $V $RT/route.py dp.kicad_pcb out.kicad_pcb NET1 NET2 ... \
  --keep-input-copper $(python scripts/apply_constraints.py constraints.yaml --router-flags)

# 3. ground return vias next to signal vias
$V $RT/route_planes.py out.kicad_pcb final.kicad_pcb --nets GND GND \
  --plane-layers B.Cu F.Cu --skip-existing-zones --add-gnd-vias --stitch-vias --stitch-pitch 5
```

Reversed, the pair gets routed last into whatever gaps remain. Measured on one
board: single-ended-first gave 13.4 mm skew and 8-vs-3 vias; pairs-first gave
5.2 mm and 0-vs-3.

Exclude the plane net (`GND`) from stage 2 - the pour carries it. But note a
pour alone connects nothing across layers: SMD pads need the pour on **their**
layer, plus stitching vias to reach the other side.

## Repairs every run needs

The Rust router has two known defects. Check both, every time:

1. **It rewrites `.kicad_pro` design rules** to match what it produced - dropping
   edge clearance, hole clearance, nulling `min_annular_width`. Restore yours:
   `apply_constraints.py constraints.yaml --project BOARD.kicad_pro`, then re-run
   DRC. On the validated run the result still passed at full strictness, so the
   relaxation was never necessary.
2. **It discards copper zones.** The ground plane vanished from one run's output.
   `verify.py` has a `plane:` check for exactly this.

## Ground pours

- `connect_pads: solid`. Thermal relief starves 0603 pads - it produced 13
  `starved_thermal` errors on a board where solid gave none.
- **Shape the outline to exclude RF keepouts.** A footprint's own keepout was
  honoured on B.Cu and ignored on F.Cu.
- Refill after any copper change, or DRC reports hundreds of phantom clearance
  errors from stale fill polygons tracing the *old* routing.
  Refill headlessly - no GUI, no MCP:  `python scripts/fill_zones.py BOARD.kicad_pcb`

## Recording a run

`KICAD_ROUTE_TRACE=1` writes `*_routetrace.json`. Replay it:

```bash
$V $RT/animate_route.py final-out_routetrace.json --board dp.kicad_pcb -o routing.gif --size 900 --fps 6
```

The router finishes in under a second, so this is the only way to watch it.

## Then verify

```bash
python scripts/verify.py BOARD.kicad_pcb --spec constraints.yaml --sch BOARD.kicad_sch \
   --project BOARD.kicad_pro --lock rules.lock.json
kicad-cli pcb drc --format json --severity-error --output drc.json BOARD.kicad_pcb
```

Both. `kicad-cli` DRC does not check keepout intrusion, pair skew, via symmetry,
board-vs-schematic net sync, or rule drift - all of which have been live defects
while DRC reported zero errors.
