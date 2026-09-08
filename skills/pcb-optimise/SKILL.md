---
name: pcb-optimise
description: Measure and improve an existing KiCad board - baseline its routing quality, re-route selected nets under tighter constraints, and compare before/after. Use when asked to optimise, clean up, reduce vias, fix a differential pair, improve EMI, or assess a board someone else designed.
---

# Optimising an existing board

Never re-route blind. Measure, change one thing, measure again, keep the winner.

## 1. Baseline and protect

```bash
git add -A && git commit -m "baseline before optimisation"
python scripts/verify.py BOARD.kicad_pcb --spec constraints.yaml --json before.json
kicad-cli pcb drc --format json --output drc-before.json BOARD.kicad_pcb
```

Record: copper length, via count, per-layer split, pair skew, DRC counts. If the
board has no `constraints.yaml`, infer one from the board and **confirm it with
the user** - fab tier and impedance targets cannot be read off a `.kicad_pcb`.

## 2. Decide what to change

| Symptom | Lever |
|---|---|
| Too many vias | raise `--via-cost` (default 75 = 5 mm of path); lower `budgets.max_vias` |
| Routes wander | lower `--heuristic-weight` toward 1.0 - optimal but slower |
| Pair skew / asymmetric vias | re-route the pair with `route_diff.py` first |
| EMI concerns | `--track-proximity-cost` > 0, symmetric pair vias, unbroken return path |
| Plane fragmented | fewer signals on the plane layer; more stitching |
| Fails fab | fix `constraints.yaml` floors, then re-apply and re-route |

## 3. Re-route selectively

Rip only what you intend to change - `--rip-existing-nets PATTERN` with an
explicit `--nets` scope, or `--keep-input-copper` to preserve everything else.
Protected nets (length-matched groups, routed pairs) are skipped unless named
exactly. Plane nets are never ripped by `route.py`; use `route_planes.py`.

## 4. Compare honestly

Report both directions. A change that cuts copper 28% but triples via count is a
tradeoff, not an improvement - say so and let the user choose. Keep both results
in git so either can be restored.

Worked example, one board, two routers:

| | Freerouting 2.1.0 | Rust A* |
|---|---|---|
| Routing violations | 1 unconnected, 2 dangling | 0 |
| Copper | 958.3 mm | 691.7 mm |
| Vias | 17 | 41 |
| Runtime | 58 s (`-mp 20`, measured) | 0.18 s — ~320x faster |

The Rust router won on completeness and copper, lost on vias, and needed its
output repaired (rules rewritten, zones discarded). All three facts belong in
the report.
