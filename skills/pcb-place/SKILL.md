---
name: pcb-place
description: Improve PCB component placement automatically - simulated annealing against a weighted cost function covering ratsnest length, decoupling distance, courtyard overlap, keepouts and board area. Use when asked to optimise, tidy or improve a placement, to shorten routes, to fit a board into less space, or before routing a board that was placed by hand.
---

# Placement optimisation

```bash
python scripts/place_optimise.py BOARD.kicad_pcb --sch BOARD.kicad_sch \
       --spec constraints.yaml --iters 15000 --restarts 3 \
       --strip-tracks --out optimised.kicad_pcb
```

**Refines, never re-places from scratch.** Anchors (`U*`, `J*`, `H*`, `MH*` by
default) never move; everything else may stray up to `move_radius_mm` from where
you put it. The result stays recognisable and cannot wreck a layout you like.

## Order of operations

Placement optimisation comes **before** routing. Moving parts under existing
copper leaves every track pointing at where a pad used to be - hundreds of
shorts, not a degraded route. The script refuses to run on a routed board unless
you pass `--strip-tracks`.

```
optimise -> route_diff -> route -> fill_zones -> verify
```

## Cost function

A weighted sum, tunable per board under `placement_cost` in `constraints.yaml`:

| Term | Default | Meaning |
|---|---|---|
| `ratsnest_mm` | 1.0 | star-model airwire length - routability proxy |
| `decap_violation` | 50.0 | per mm a decoupling cap sits past its band limit |
| `overlap` | 500.0 | per mm² of courtyard collision |
| `keepout` | 500.0 | per mm² of a part inside a declared keepout |
| `off_board` | 1000.0 | per mm hanging off the outline |
| `connector_edge` | 20.0 | per mm a connector sits away from an edge |
| `area_mm2` | 0.5 | penalises sprawl |

`placement_band: lf|hf|rf` sets the decoupling and crystal limits.

**Overlap, keepout and off-board are also hard rejections**, not just expensive.
A weighted overlap term is always worth buying when the ratsnest gain is larger -
which it usually is. Weighted-only, the optimiser produced 21 DRC errors while
reporting an overlap cost of 0.02 mm². The weights still report and rank; the
hard check is what guarantees legality.

## Decoupling is scored per power PIN, not per capacitor

Scoring per capacitor lets every cap cluster around one convenient pin and report
a perfect result while another pin is starved. On the example board the ESP32's
3V3 pin sat **22 mm** from its nearest 100 nF while the model reported a
near-zero violation, because all the caps were close to the regulator.

Distances are measured **pad to pad on the rail** - that is the real trace
length, and with the ground return it is the loop area that decides whether the
cap does anything.

## Freedoms are derived, not listed

Over-constraining placement is what starves pins. Three rules, all automatic:

- **An anchor on a board edge may slide ALONG that edge.** A connector is fixed
  because it must stay reachable - that pins one axis, not two.
  (`slide_edge_anchors`, on by default; override per part in `slide`.)
- **A part that owns a footprint keepout stays pinned.** Its keepout is anchored
  in board coordinates, so moving the part would silently decouple the two - an
  RF module would drift away from its antenna exclusion zone.
- **A keepout owner's courtyard is clipped to its physical body** for the overlap
  test. Some footprints draw the courtyard around the keepout as well, so an RF
  module appears to collide with everything nearby. Courtyard is physical
  clearance; keepout is electrical exclusion. They are scored separately.

## Targeted relocation

A pure random walk never makes a 12 mm jump, because every intermediate position
is worse - so the optimiser plateaus with a pin unserved while legal positions
sit empty beside it. 18% of proposals instead pick the worst-served power pin and
try moving a suitable capacitor **directly** to a legal spot next to it.

That single operator took the example board's starved pin from 15.9 mm to
**4.09 mm**, and the total decoupling violation from 43.6 mm to 7.9 mm.

## Why annealing

The cost surface has many local minima - swapping two capacitors is a discrete
jump, not a gradient. Accepting occasional uphill moves is what escapes them.
15% of moves are swaps between same-prefix parts, which is what fixes decap
ordering (smallest cap nearest the pin).

Use `--restarts 3` or more: different seeds land in different minima and the
best is kept. `--dry-run` scores without writing.

## Measured on the example board

Against a hand-placed, hand-tuned layout:

| | hand-placed | optimised |
|---|---|---|
| copper | 772.4 mm | **661.0 mm** |
| vias | 92 | **42** |
| ESP32 3V3 pin to nearest 100nF | 22.00 mm | **4.09 mm** |
| DRC after re-route | 0 | **0** |
| placement score | 50/100 | **60/100** |

USB pair skew moved 1.52 -> 2.89 mm, the one term that got worse - it crosses
the 2.5 mm check threshold. Report trades like that; do not bury them.

## Verify, always

The optimiser scores **geometry, not connectivity**. It has no idea whether the
result routes. Always re-route and run DRC plus `verify.py` afterwards, and
report the DRC delta honestly - a placement that scores better but routes worse
is not an improvement.
