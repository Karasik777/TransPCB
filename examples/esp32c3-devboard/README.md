# ESP32-C3 dev board

The board TransPCB was built while making. 2 layers, 30 x 60 mm, JLCPCB standard.

![board](media/board.png)

## What's on it

- **U1** ESP32-C3-WROOM-02, antenna at the top board edge with a copper keepout
- **J3** USB-C, right-angle through-hole (GCT USB4085), 5.1k CC pulldowns
- **U3** USBLC6-2SC6 ESD clamp on D+/D- and VBUS
- **U2** AP2112K-3.3 LDO; CP1 100uF radial bulk, C4/C5 in, C6/C7 out
- Native USB Serial/JTAG on IO18/IO19 - **no UART bridge chip**
- RESET and BOOT buttons, power LED plus two user LEDs, six test points
- **J1/J2** 1x10 headers, pinout matched to the module's pin geography

## Header pinout

Deliberately arranged so each header carries the module pins physically nearest
it. Getting this wrong forces nets across the whole board and makes a 2-layer
route impossible without cutting the ground plane.

| J1 (left) | J2 (right) |
|---|---|
| 1 +3V3 · 2 GND | 1 +3V3 · 2 GND |
| 3 IO4 · 4 IO5 · 5 IO6 · 6 IO7 | 3 IO0 · 4 IO1 · 5 IO2 · 6 IO3 |
| 7 IO8 · 8 IO9 | 7 IO21/TX · 8 IO20/RX · 9 IO10 |
| 9 GND · 10 +3V3 | 10 EN |

## Reproducing

```bash
cp -r examples/esp32c3-devboard /tmp/demo && cd /tmp/demo
../../scripts/preflight.sh beautiful.kicad_pcb
python ../../scripts/verify.py beautiful.kicad_pcb --spec constraints.yaml --sch beautiful.kicad_sch
```

## Routing animation

`media/routing.gif` - 46 frames replaying the router's own event log
(`KICAD_ROUTE_TRACE=1`), showing traces laid, ripped and restored. Open it in a
browser; some image viewers show only the first frame.

## State

**0 DRC errors, 0 unconnected pads.** 5 of 6 TransPCB checks pass.

Placement was refined by `place_optimise.py` (simulated annealing) and fully
re-routed. Against the hand-placed original:

| | hand-placed | optimised |
|---|---|---|
| copper | 772.4 mm | **661.0 mm** |
| vias | 92 | **42** |
| ESP32 3V3 pin to nearest 100 nF | 22.00 mm | **4.09 mm** |
| DRC errors | 0 | **0** |

That last row is the one that matters: decoupling 22 mm from the pin it serves
is ornamental. It also drove four fixes in the optimiser - see the
`pcb-place` skill.

Routed with the two-stage flow - `route_diff.py` for the USB pair first, then
`route.py` for the rest. Pair skew: connector side 7.39 -> 3.26 mm, MCU side
13.42 -> 5.16 mm against a single-ended first attempt.

A board-level `ANTENNA_KEEPOUT` zone (tracks, vias and pour not allowed) keeps
the RF region clear - the router respects it, rather than the verifier catching
intrusions afterwards. Its boundary is y=61.5 rather than the footprint's 61.9,
because U1's own pads sit at y=62.0 and a 0.4 mm track reaching them spans
61.8..62.2.

### The one accepted failure

`diffpair:USB` - copper skew **2.89 mm** against a 2.5 mm threshold
(`USB_DP` 85.19 mm / 2 vias, `USB_DM` 82.29 mm / 4 vias).

This is the term that regressed when placement was optimised for decoupling:
skew was 1.52 mm before. A deliberate trade, and the right one here - at 12 Mbps
full-speed a bit period is 83 ns, and 2.9 mm of FR4 is roughly 19 ps, under
0.03% of a bit. USB 2.0 full speed also has no impedance-matching requirement;
that begins at high speed, 480 Mbps.

It would matter on a faster interface or in EMC testing. Fixing it properly
means placement work around TP3/TP4 and the ESD part, not re-routing.
