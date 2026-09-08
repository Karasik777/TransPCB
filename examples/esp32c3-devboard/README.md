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

## Known state

This board is a **teaching example, not a verified design.** `verify.py` fails
two checks on it deliberately, and both are instructive:

- `keepout:esp32c3-antenna` - a +3V3 track at y=61.20 runs above the module's
  pads, inside the antenna region. A real intrusion the autorouter introduced.
- `diffpair:USB` - via count 0 vs 3 on D+/D-. Asymmetric vias convert
  differential to common mode.

Neither breaks a 12 Mbps full-speed USB link, which is why they survived. Both
would matter at high speed or in EMC testing. Fix them before ordering.
