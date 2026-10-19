#!/bin/bash
# ResolveBridge installer (macOS). Run from anywhere; safe to run again after `git pull`.
#
#   ./install.sh            set up everything
#   ./install.sh --no-mcp   skip registering the MCP server with Claude Code
#
# 1. .venv/ with requirements.txt        (needs Python 3.10+)
# 2. runtime/ + shared-secret token       (runtime/token.lua, mode 600)
# 3. bin/ : Swift helpers in utils/       (optional; needs Xcode Command Line Tools)
# 4. claude_fusion_bridge.lua -> Resolve's Fusion/Scripts/Utility, with this clone's path written in
# 5. `claude mcp add fusion-bridge`       (user scope)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTER_MCP=1
for a in "$@"; do
	case "$a" in
		--no-mcp) REGISTER_MCP=0 ;;
		-h|--help) sed -n '2,11p' "$0"; exit 0 ;;
		*) echo "unknown option: $a" >&2; exit 2 ;;
	esac
done

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }

[ "$(uname)" = "Darwin" ] || { echo "ResolveBridge supports macOS only" >&2; exit 1; }

# --- 1. Python environment ---------------------------------------------------
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
	if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
		PY="$(command -v "$c")"; break
	fi
done
[ -n "$PY" ] || { echo "Python 3.10+ is required (e.g. brew install python@3.12)" >&2; exit 1; }

VENV="$ROOT/.venv"
if [ -d "$VENV" ] && ! "$VENV/bin/python" -c 'import sys' >/dev/null 2>&1; then
	warn ".venv is broken (moved or Python removed); recreating it"
	rm -rf "$VENV"
fi
if [ ! -d "$VENV" ]; then
	say "Creating .venv with $PY"
	"$PY" -m venv "$VENV"
fi
say "Installing Python packages"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$ROOT/requirements.txt"

# --- 2. runtime/ and token ---------------------------------------------------
say "Preparing runtime/ and the auth token"
RESOLVE_BRIDGE_HOME="$ROOT" "$VENV/bin/python" "$ROOT/mcp/bridge_client.py" token >/dev/null

# --- 3. Swift helpers --------------------------------------------------------
if command -v swiftc >/dev/null 2>&1; then
	say "Building Swift helpers into bin/"
	mkdir -p "$ROOT/bin"
	for src in "$ROOT"/utils/*.swift; do
		name="$(basename "$src" .swift)"
		if [ ! -x "$ROOT/bin/$name" ] || [ "$src" -nt "$ROOT/bin/$name" ]; then
			swiftc -O -suppress-warnings -o "$ROOT/bin/$name" "$src" || warn "failed to build $name"
		fi
	done
else
	warn "swiftc not found; skipping utils/*.swift (install with: xcode-select --install)"
fi

# --- 4. Lua bridge into Resolve ---------------------------------------------
SCRIPTS="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
[ -d "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve" ] ||
	warn "DaVinci Resolve support folder not found; installing the script anyway (start Resolve once if it does not show up)"
say "Installing claude_fusion_bridge.lua into Resolve"
mkdir -p "$SCRIPTS"
ROOT="$ROOT" OUT="$SCRIPTS/claude_fusion_bridge.lua" "$VENV/bin/python" - "$ROOT/resolve/claude_fusion_bridge.lua" <<'PY'
import os, sys
src = open(sys.argv[1], encoding="utf-8").read()
root = os.environ["ROOT"].replace("\\", "\\\\").replace('"', '\\"')
assert '"@RESOLVE_BRIDGE_ROOT@"' in src, "placeholder missing in claude_fusion_bridge.lua"
open(os.environ["OUT"], "w", encoding="utf-8").write(src.replace('"@RESOLVE_BRIDGE_ROOT@"', '"%s"' % root, 1))
PY

# --- 5. MCP server -----------------------------------------------------------
MCP_CMD=("$VENV/bin/python" "$ROOT/mcp/fusion_bridge_mcp.py")
if [ "$REGISTER_MCP" = 1 ]; then
	if command -v claude >/dev/null 2>&1; then
		say "Registering MCP server fusion-bridge with Claude Code"
		claude mcp remove fusion-bridge -s user >/dev/null 2>&1 || true
		claude mcp add fusion-bridge -s user -- "${MCP_CMD[@]}"
	else
		warn "claude CLI not found; register later with:"
		echo "  claude mcp add fusion-bridge -s user -- ${MCP_CMD[*]}"
	fi
fi

cat <<EOF

Done. Next:
  1. (Re)start DaVinci Resolve, open a project, go to the Fusion page
  2. Workspace > Scripts > claude_fusion_bridge   (once per Resolve launch)
  3. Check:  "$VENV/bin/python" "$ROOT/mcp/bridge_client.py" status
EOF
