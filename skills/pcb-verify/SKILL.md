---
name: pcb-verify
description: Independently verify a KiCad board - board-vs-schematic net sync, RF/keepout copper intrusion, differential pair skew and via symmetry, drill and annular DFM floors, ground plane presence, and design-rule drift. Use after any routing or placement change, before ordering, or when auditing a board someone else produced. Catches defects that KiCad DRC reports as clean.
---

# Verifying a board

`kicad-cli pcb drc` is necessary but not sufficient. Every defect below was live
on a real board **while DRC reported zero errors**:

| Defect | What claimed it was fine |
|---|---|
| 4 pads on stale nets - USB D+/D- went nowhere | `pcb_sync_from_schematic`: "CLEAN, 100% pad coverage" |
| F.Cu pour filling the RF antenna keepout | footprint declared `copperpour not_allowed` |
| Autorouter silently loosened 12-20 design rules | router reported "0 violations" (against its own weakened rules) |
| Ground plane discarded entirely by the router | DRC saw no error; GND rode on stitching alone |
| Diff pair with 13 mm skew, 8-vs-3 vias | DRC has no concept of a pair |

## Run

```bash
python scripts/verify.py BOARD.kicad_pcb \
  --spec constraints.yaml \
  --sch BOARD.kicad_sch \
  --project BOARD.kicad_pro \
  --lock rules.lock.json \
  --json report.json
```

Exit 1 on any failure - suitable for CI or a pre-order gate. Then always also:

```bash
kicad-cli pcb drc --format json --severity-error --output drc.json BOARD.kicad_pcb
```

## The checks

- **net-sync** - exports the schematic netlist and compares every `(ref, pin)`
  against the board's pad nets. Catches footprints carried across a schematic
  change with stale nets, which sync tools do not fix and do not report.
- **keepout** - point-in-polygon against pours, tracks and vias for each declared
  rect. Never trust a footprint's own keepout zone.
- **diffpair** - copper length skew against `max_skew_mm`, plus via-count
  symmetry. Asymmetric vias convert differential to common mode, the main
  radiated-emissions driver on USB.
- **dfm-drill** - every pad and via drill against the fab floor, and annular ring
  `(size - drill) / 2`. Module footprints commonly ship 0.2 mm thermal vias that
  fail a 0.3 mm standard-tier order.
- **plane** - a declared plane net must have a filled zone on each declared layer.
- **rule-drift** - current `.kicad_pro` rules against a locked snapshot.

## Interpreting failures

**Keepout hits at a module's pad row are usually legitimate** - the pads may
overlap the footprint's own keepout band, and tracks must reach them. Check the
`y` extent: copper *above* the pads is a real intrusion, copper *at* pad level
is not. Tighten the rect rather than ignoring the check.

**Zone-related DRC errors in bulk** (hundreds of clearance violations, all naming
a Zone) mean stale fill, not a routing problem. Refill and re-run.

## Reporting

Give the user the failing checks with concrete numbers, and say plainly which
are cosmetic (silkscreen overlap, isolated pour fragments) versus electrical
(net mismatch, keepout intrusion, missing plane). Do not report a board as clean
on the strength of DRC alone.
