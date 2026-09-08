---
name: pcb-constraints
description: Define, validate and apply PCB design constraints - fab limits, net classes, diff pairs, via budgets, impedance targets, keepouts, planes - and push them down to the autorouter and the verification suite. Use when starting a board, changing fab vendor or tier, adding a differential pair or impedance requirement, capping via count, or whenever a router has rewritten the design rules.
---

# PCB constraints

Constraints live in **one YAML file** per board, not in the `.kicad_pro`. The
autorouter rewrites `.kicad_pro` rules to match whatever it produced, so treating
KiCad as the source of truth silently loses your fab limits.

## The flow

```
constraints.yaml
      |
      +--> apply_constraints.py --project board.kicad_pro   (KiCad rules + net classes)
      +--> apply_constraints.py --router-flags              (autorouter CLI flags)
      +--> verify.py --spec constraints.yaml                (checked after routing)
```

Start from `constraints/example.yaml`. Every field is documented inline.

## Rules

1. **Validate before routing.** `apply_constraints.py SPEC` exits non-zero if any
   value sits below its fab floor - track width, clearance, drill, annular ring,
   diff-pair gap. Fix the spec; never lower the floor to make a design pass.

2. **Re-apply after every routing pass.** The Rust router rewrites 12-20 rule
   values in `.kicad_pro`. Snapshot with `--lock rules.lock.json` before routing,
   and `verify.py --lock` reports the drift afterwards.

3. **Budgets are cost weights, not hard caps.** `max_vias` scales `--via-cost`,
   which makes vias expensive relative to path length (default 75 = 5 mm of
   routing). The router still places one where the alternative is failing to
   route. If you need a hard cap, verify afterwards and re-run with a higher cost.

4. **Impedance needs a real stackup.** `--impedance` computes track width from
   the board stackup. Set the stackup first, or you get a width derived from
   KiCad's defaults, which are unlikely to match your fab.

## Translating an intent into constraints

| The user says | Set |
|---|---|
| "keep it cheap / JLC standard" | `fab.tier: standard`, drill 0.30, clearance 0.15 |
| "fewer vias" | `budgets.max_vias` lower - raises via cost |
| "USB / Ethernet / HDMI pair" | a `diff_pairs` entry with `gap_mm` + `max_skew_mm` |
| "90 ohm USB" | `impedance_ohm: 90` on the pair, plus a defined stackup |
| "don't route under the antenna" | a `keepouts` rect - see the RF note below |
| "solid ground" | a `planes` entry, `connect_pads: solid` |
| "better EMI" | `budgets.spread_tracks: true`, and keep via counts symmetric on pairs |

## RF keepouts

Do **not** rely on a footprint's own keepout zone. On this stack a module's
`copperpour not_allowed` keepout was honoured on B.Cu and ignored on F.Cu -
the pour filled straight through the antenna. Declare the region in
`constraints.yaml` and let `verify.py` prove it with a point-in-polygon test.
Shape the zone outline to exclude the region as well; belt and braces.
