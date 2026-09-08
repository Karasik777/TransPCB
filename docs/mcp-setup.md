# MCP setup

TransPCB drives KiCad through [`kicad-mcp-pro`](https://github.com/oaslananka/kicad-mcp-pro)
(MIT, Osman Aslan). It is **not vendored** here - `setup/install.sh` installs a
pinned version so upstream keeps its own attribution and updates.

## Install

```bash
./setup/install.sh --project /path/to/your/board
```

## Configuration

Both clients need the same block. **`KICAD_MCP_ENABLE_EXPERIMENTAL_TOOLS=1` is
required** - without it, all 15 routing tools and the live-edit tools are
filtered out of the tool list entirely.

**VS Code** - `<project>/.vscode/mcp.json` (written by the installer):

```json
{
  "servers": {
    "kicad": {
      "type": "stdio",
      "command": "~/.local/bin/kicad-mcp-pro",
      "args": [],
      "cwd": "${workspaceFolder}",
      "env": {
        "KICAD_MCP_PROJECT_DIR": "${workspaceFolder}",
        "KICAD_MCP_PROFILE": "agent_full",
        "KICAD_MCP_OPERATING_MODE": "experimental",
        "KICAD_MCP_ENABLE_EXPERIMENTAL_TOOLS": "1"
      }
    }
  }
}
```

**Claude Code** - the same object under `projects['<abs path>'].mcpServers.kicad`
in `~/.claude.json`, then `/mcp` to connect.

Each client spawns its **own** server process. Both can read the same KiCad
instance, but do not let two agents write at once.

## How the server reaches KiCad

Two paths, and it falls back silently:

- **live-gui** - KiCad's IPC API over `/tmp/kicad/api.sock`. Requires
  `api.enable_server: true` in `~/.config/kicad/<ver>/kicad_common.json` **and**
  an editor frame open. The handlers are registered by the PCB/schematic editor
  windows and vanish when they close.
- **file-backed** - parses the `.kicad_pcb` directly. Read-only for most purposes.

Check which is active: `pcb_get_board_summary` -> `metadata.source`.

Symptom of the project manager being open but no editor frame:
`no handler available for request of type kiapi.common.commands.GetOpenDocuments`.

**Run `pcbnew` standalone, never through the project manager** - see
[gotchas.md](gotchas.md) for the segfault this avoids.

## Operating modes

| Mode | Unlocks |
|---|---|
| `readonly` | inspection only |
| `write` | schematic and board edits |
| `manufacturing` | gerbers, BOM, release gates |
| `experimental` | **routing**, live transactions, pin/gate swapping |

`enable_experimental_tools` forces `experimental` regardless of
`KICAD_MCP_OPERATING_MODE`, so setting the flag is enough.

## Verifying

```bash
kicad_get_version          # CLI path, IPC status
pcb_get_board_summary      # metadata.source should read live-gui
pcb_get_live_edit_state    # transaction_supported after a begin_commit
```
