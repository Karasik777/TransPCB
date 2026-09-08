---
name: pcb-new
description: Design a KiCad board from scratch - schematic capture from a spec, component and footprint selection, ERC-clean netlist, then placement with keepout, courtyard and fan-out awareness. Use when asked to create a new board, add a subsystem to an empty project, or turn a requirements description into a schematic and layout.
---

# A board from scratch

Order: **constraints -> schematic -> placement -> routing**. Each stage must be
clean before the next; a placement mistake costs a full re-route to fix.

## 1. Constraints first

Copy `constraints/example.yaml` and fill it in. Ask the user for anything you
cannot infer - fab vendor and tier, impedance targets, board size limits,
connector positions. See the `pcb-constraints` skill.

## 2. Schematic

Verify every symbol and footprint **exists** before building:

```
lib_get_symbol_info / lib_search_symbols       -> pin numbers and names
lib_get_footprint_info                          -> pad count must match the symbol
```

Then `sch_build_circuit` with `auto_layout=True`. It replaces the whole sheet, so
pass the complete netlist each time. It connects by named labels rather than
routed wires, which cannot short by geometry.

Check pin **numbering**, not just names - `Device:LED` is pin 1 = K, pin 2 = A,
and a module's thermal paddle is usually a real pin that must be tied to GND.

Add `sch_add_no_connect` for genuinely unused pins (USB SBU1/SBU2, regulator NC)
or ERC will fail. Then:

```
run_erc      # must be 0 before placement
```

## 3. Placement

Read the footprint geometry before you place. Assumptions that have cost a
re-place:

- **Header pin origin is pin 1, not the centre.** `PinHeader_*_Vertical` pads run
  along **Y**; `rot=0` is a vertical column, `rot=90` lays it flat.
- **Connector mating faces vary.** Check which end the body extends toward -
  a right-angle USB-C had its opening at local +Y, so `rot=180` pointed it into
  the board.
- **Courtyards can dwarf the body.** A tactile switch measured 7.8 x 5.5 mm
  courtyard around a 3 mm body.
- **RF modules carry an antenna keepout** in the footprint, often as a zone that
  the courtyard encloses - so courtyard overlaps with nearby parts are a
  bounding-box artifact, not a collision. But the keepout itself is real:
  no copper, no parts, and put it at a board edge.

Place with routing in mind. **Match header pinout to the module's pin geography** -
if a GPIO leaves the left side of the module, it belongs on the left header.
Getting this wrong forced 6 nets across the full board width and made a 2-layer
route impossible without cutting the ground plane. Fixing the pinout reduced it
to one crosser.

Verify placement before routing:

```
kicad-cli pcb drc ...      # courtyard overlaps, edge clearance, hole-to-hole
python scripts/verify.py BOARD.kicad_pcb --spec constraints.yaml --sch BOARD.kicad_sch
```

Expect unconnected items at this stage - nothing is routed yet.

## 4. Sync trap

`pcb_sync_from_schematic` **only adds footprints**. It does not update nets on
pads that already exist, yet still reports "CLEAN, 100% pad coverage". After any
schematic net change, run the `net-sync` check - it compares the exported
netlist against every board pad and is the only thing that catches this.

## Then route

Hand off to the `pcb-route` skill.
