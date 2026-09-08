# Workflow

## New board

```bash
cp constraints/example.yaml myboard/constraints.yaml   # edit: fab, pairs, keepouts
cd myboard && git init && git add -A && git commit -m "start"
```

Then ask Claude, in plain language:

> Build me an ESP32-C3 dev board: USB-C power and native USB data, an efficient
> LDO, ESD protection, reset and boot buttons, two GPIO headers and test points.
> JLCPCB standard spec, 2 layers, small as you can make it.

Claude loads `pcb-new`, and works through: constraints -> parts and footprint
verification -> `sch_build_circuit` -> ERC clean -> placement -> DRC ->
`pcb-route` -> `pcb-verify`.

## Routing an existing board

> Route this board. Keep vias under 60, USB as a matched pair, ground plane both
> layers, and nothing under the antenna.

Everything after "route this board" belongs in `constraints.yaml` - say it once
in the file and it reaches the router, the rules and the verifier together.

## Optimising someone else's board

> Look at this board and tell me what's wrong with it, then improve the routing.

Claude loads `pcb-optimise`: baselines it, infers a constraint spec (confirming
fab tier and impedance targets with you, since those aren't in the file), then
changes one lever at a time and reports both directions of every tradeoff.

## The loop, whichever path

```
preflight  ->  commit  ->  apply constraints  ->  act  ->  verify  ->  commit
```

`preflight.sh` before touching a live board. `git commit` before every routing
run - both routers and an open pcbnew have each destroyed a full pass.

## What to expect Claude to push back on

- A constraint below a fab floor - it will refuse and ask you to fix the spec
  rather than lower the floor
- Routing before ERC is clean
- Reporting a board as "clean" on DRC alone - `verify.py` checks five things DRC
  does not, and each has been a live defect while DRC reported zero errors
- A tradeoff presented as a win - fewer vias usually means more copper, and both
  numbers get reported
