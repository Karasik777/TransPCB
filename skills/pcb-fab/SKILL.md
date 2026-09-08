---
name: pcb-fab
description: Produce an orderable manufacturing package from a KiCad board - gerbers, Excellon drill, BOM and pick-and-place with vendor quirks applied, gated on a clean DRC. Use when asked to order a board, generate gerbers or fab files, prepare for assembly, or check whether a design is ready to manufacture.
---

# Fab package

```bash
python scripts/fab_package.py BOARD.kicad_pcb --sch BOARD.kicad_sch --out fab/ --vendor jlcpcb
```

**It refuses to run on a board with DRC errors or unconnected pads.** That is the
point - a fab package that isn't orderable is worse than none, because it looks
finished. `--force` exists for deliberate prototypes; say so in the report if used.

## Before packaging

Run the full gate, not just DRC:

```bash
./scripts/preflight.sh BOARD.kicad_pcb --close
python scripts/fill_zones.py BOARD.kicad_pcb
python scripts/verify.py BOARD.kicad_pcb --spec constraints.yaml --sch BOARD.kicad_sch \
       --project BOARD.kicad_pro --lock rules.lock.json
```

Stale zone fill alone will produce hundreds of phantom clearance errors and block
the gate for no real reason.

## What comes out

| File | Use |
|---|---|
| `<name>-jlcpcb.zip` | gerbers + Excellon drill + map — upload this |
| `<name>-cpl.csv` | pick-and-place, JLC columns: Designator, Mid X, Mid Y, Layer, Rotation |
| `<name>-bom.csv` | Comment, Designator, Footprint, Qty, LCSC |
| `drc.json` | the gate's own evidence |

## Vendor quirks that bite

- **Rotation.** JLC's expected orientation differs from KiCad's for many
  polarised parts — diodes, electrolytics, ICs. Always check the JLC preview
  before paying. The script warns; it cannot fix this reliably per-part.
- **LCSC part numbers.** Assembly needs an `LCSC` field on each symbol. Without
  it the BOM column is blank and the order stalls. Add them during schematic
  capture, not at order time.
- **Drill floor.** 0.3 mm is standard tier; 0.2 mm costs more. Module footprints
  commonly ship 0.2 mm thermal vias — `verify.py`'s `dfm-drill` check catches this.
- **Protel extensions off**, job file on. Already set.

## Reporting

Tell the user the file paths, the part count, and what you could not verify —
specifically rotations and LCSC coverage. Never say a board is "ready to order"
on DRC alone.
