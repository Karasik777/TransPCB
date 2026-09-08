# Gotchas

Every item here was hit for real, cost time, and is not documented upstream.

## KiCad 10.0.5 segfaults on live board commits when eeschema is loaded

Opening the board through the KiCad **project manager** loads both kifaces into
one process. A *board* commit request is then dispatched to eeschema's API
handler, which null-derefs and takes the whole application down:

```
KICAD_API_SERVER::handleApiEvent
  -> API_HANDLER::Handle              (libkicommon.so.10.0.5)
    -> SIGSEGV in _eeschema.kiface +0x1a3548b
```

Reproduced deterministically, identical offsets, via both `pcb_begin_commit`
(the sanctioned MCP tool) and a raw `kipy` call. How the call is made is
irrelevant.

**Workaround:** run the PCB editor standalone - `pcbnew board.kicad_pcb` - so
only `_pcbnew.kiface` loads. Then `transaction_supported: true` and the full
begin/stage/push/drop cycle works.

`scripts/preflight.sh` checks `/proc/<pid>/maps` and aborts on this state.

Related: `transaction_supported: false` on an idle transaction is the field's
uninitialised default, **not** a capability probe. It only becomes meaningful
after `pcb_begin_commit`.

## An open pcbnew will overwrite your file edits

pcbnew holds the board in memory. If you edit the `.kicad_pcb` on disk and that
window later saves, your changes are gone - silently. This destroyed a complete
routing pass twice during development.

Close pcbnew before file-based edits, or File > Revert after them. `preflight.sh`
warns when the file is newer than the pcbnew process that opened it.

**Commit before every routing run.** Recovery was `git checkout HEAD --` both times.

## pcb_sync_from_schematic does not update existing pads

It adds missing footprints. It does **not** re-net pads on footprints already
present - while reporting `Transfer quality: CLEAN (100.0% pad coverage)`.

After a schematic pinout change, four `U1` pads still carried nets from the
previous revision; USB D+/D- had no path to the MCU. Nothing flagged it.

Use `verify.py`'s `net-sync` check after any schematic change.

## Footprint keepouts are not honoured on every layer

The ESP32-C3 module footprint declares `(keepout (copperpour not_allowed))` on
`F.Cu` and `B.Cu`. The B.Cu pour respected it; the **F.Cu pour filled straight
through the antenna**. Confirmed by point-in-polygon: 15/15 sample points inside
copper on F.Cu, 0/15 on B.Cu.

Declare RF regions in `constraints.yaml`, shape the zone outline to exclude them,
and verify with `verify.py`. Do not eyeball a render - the fill boundary traces
the keepout edge convincingly whether or not it respected it.

## The Rust autorouter rewrites your design rules

`KiCadRoutingTools` writes back into `.kicad_pro` "to match the routed floors":

```
min_copper_edge_clearance  0.5  -> 0.2
min_hole_clearance         0.25 -> 0.15
min_hole_to_hole           0.25 -> 0.2
min_annular_width          0.13 -> null
min_text_height            0.8  -> null
```

Its "0 violations" is then measured against the weakened rules. Restoring the
originals and re-running DRC still passed - the relaxation was never needed.

Snapshot rules before routing (`--lock`), restore after, and check drift.

## The Rust autorouter discards copper zones

The B.Cu ground plane was absent from one run's output. Symptom: GND pads read
as unconnected, and a `check_planes` failure. Re-add and refill.

## Stale zone fill produces hundreds of phantom DRC errors

Fill polygons trace around the copper present when they were filled. After
re-routing, they overlap the new tracks: 460 clearance errors, 460 of them
naming a Zone. Refill, don't debug.

Refilling requires KiCad - `pcb_refill_zones` over MCP, or `B` in the GUI.
`kicad-cli` has no fill command.

## Thermal relief starves small pads

Default thermal relief on a ground pour produced 13 `starved_thermal` errors on
0603 pads. Use `(connect_pads yes ...)` - solid - for ground planes.

## A pour alone connects nothing across layers

An SMD pad on F.Cu is not connected by a B.Cu pour. You need the pour on the
pad's own layer *and* stitching vias. 64 GND pads read unconnected until a top
pour was added; the remainder needed vias.

## kicad-cli cannot do everything the GUI can

No `specctra` subcommand in either direction (so Freerouting needs two manual GUI
steps), and no zone fill command. Check `kicad-cli pcb export --help` before
assuming a headless path exists.

## MCP quirks

- `route_from_pad_to_pad` is broken: `'Pad' object has no attribute 'parent'`.
  Use `pcb_add_tracks_bulk` with explicit coordinates.
- Routing and live-edit tools are gated behind `KICAD_MCP_ENABLE_EXPERIMENTAL_TOOLS=1`.
  That flag alone forces `OperatingMode.EXPERIMENTAL`, superseding
  `KICAD_MCP_OPERATING_MODE`. Requires an MCP reconnect.
- DRC reports a zone's **anchor** coordinate, not island centroids, so "which
  island is orphaned" cannot be answered from the DRC report.
- The MCP server inherits process groups at spawn time. Joining the `docker`
  group after it started does not give it socket access - restart the client.
