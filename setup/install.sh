#!/usr/bin/env bash
# TransPCB installer. Idempotent - safe to re-run.
#
#   ./setup/install.sh [--project /path/to/board/dir]
#
# Installs pinned versions of everything the workflow needs. Nothing is
# vendored into this repo; upstream keeps its own attribution and updates.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT=""
[ "${1:-}" = "--project" ] && PROJECT="${2:-}"

MCP_VERSION=3.34.0
FR_IMAGE=ghcr.io/freerouting/freerouting:2.1.0
VENV="$HERE/.venv"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

say "Checking KiCad"
command -v kicad-cli >/dev/null || { echo "kicad-cli not found. Install KiCad 9 or 10 first."; exit 1; }
kicad-cli --version

say "Python environment ($VENV)"
if command -v uv >/dev/null; then
  uv venv "$VENV" -q 2>/dev/null || true
  uv pip install -p "$VENV/bin/python" -q shapely scipy numpy pillow pyyaml
else
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip shapely scipy numpy pillow pyyaml
fi
"$VENV/bin/python" -c "import shapely, scipy, PIL, yaml; print('python deps ok')"

say "KiCad MCP server (kicad-mcp-pro==$MCP_VERSION)"
if command -v uv >/dev/null; then
  uv tool install "kicad-mcp-pro==$MCP_VERSION" 2>/dev/null || \
    uv tool upgrade kicad-mcp-pro 2>/dev/null || true
else
  echo "uv not found - install from https://docs.astral.sh/uv/ then re-run"; exit 1
fi

say "Rust autorouter (KiCadRoutingTools)"
KV=$(kicad-cli --version | grep -oE '^[0-9]+\.[0-9]+' | head -1)
PLUGDIR="$HOME/.local/share/kicad/${KV}/3rdparty/plugins"
RT="$PLUGDIR/com_github_drandyhaas_kicadroutingtools"
if [ -d "$RT" ]; then
  echo "already installed: $RT"
else
  mkdir -p "$PLUGDIR"
  git clone --depth 1 https://github.com/drandyhaas/KiCadRoutingTools "$RT"
fi
ls "$RT/rust_router/"*linux*.so >/dev/null 2>&1 \
  && echo "prebuilt Rust router present (no toolchain needed)" \
  || echo "NOTE: no prebuilt .so for this platform - see the plugin README to build it"

say "Freerouting container (optional fallback)"
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  docker pull "$FR_IMAGE"
else
  echo "docker unavailable - skipping. Freerouting is optional; the Rust router is the default."
  echo "  to enable:  sudo systemctl start docker && sudo usermod -aG docker \$USER"
fi

if [ -n "$PROJECT" ]; then
  say "Wiring MCP into $PROJECT"
  mkdir -p "$PROJECT/.vscode"
  cat > "$PROJECT/.vscode/mcp.json" <<JSON
{
  "servers": {
    "kicad": {
      "type": "stdio",
      "command": "$HOME/.local/bin/kicad-mcp-pro",
      "args": [],
      "cwd": "\${workspaceFolder}",
      "env": {
        "KICAD_MCP_PROJECT_DIR": "\${workspaceFolder}",
        "KICAD_MCP_PROFILE": "agent_full",
        "KICAD_MCP_OPERATING_MODE": "experimental",
        "KICAD_MCP_ENABLE_EXPERIMENTAL_TOOLS": "1"
      }
    }
  }
}
JSON
  echo "wrote $PROJECT/.vscode/mcp.json"
  echo
  echo "For Claude Code, add the same block to ~/.claude.json under"
  echo "  projects['$PROJECT'].mcpServers.kicad"
  echo "then run /mcp to connect. EXPERIMENTAL_TOOLS=1 is required - routing and"
  echo "live-edit tools are gated behind it."
fi

say "Installing skills"
mkdir -p "$HOME/.claude/skills"
for s in "$HERE"/skills/*/; do
  n=$(basename "$s")
  ln -sfn "$s" "$HOME/.claude/skills/$n"
  echo "  linked $n"
done

say "Done"
cat <<'MSG'
Next:
  1. cp constraints/example.yaml  <your-board>/constraints.yaml   and edit it
  2. ./scripts/preflight.sh <board>.kicad_pcb      # before any live edit
  3. Ask Claude: "route this board using the TransPCB workflow"
MSG
