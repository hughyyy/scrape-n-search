#!/usr/bin/env bash
#
# scrape-n-search — machine-level install: create a dedicated venv, install the
# MCP server package, and register it with any detected harnesses.
#
#   ./install.sh               install + register with detected harnesses
#   ./install.sh register      re-register (e.g. after adding a harness)
#   ./install.sh list          show detection + registration state
#   ./install.sh uninstall     unregister from harnesses, then remove the venv
#
# Registration uses a *dedicated* venv (not the repo's .venv) so the command
# path embedded in harness configs stays stable even if the repo moves.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/scrape-n-search"
VENV="$DATA/venv"
PYTHON="${PYTHON:-python3}"
MODE="${1:-install}"

case "$MODE" in
  install)
    if [[ ! -x "$VENV/bin/python" ]]; then
      echo "creating venv: $VENV"
      "$PYTHON" -m venv "$VENV"
      "$VENV/bin/pip" install -q --upgrade pip
    fi
    echo "installing scrape-n-search into $VENV"
    "$VENV/bin/pip" install -q -e "$REPO/mcp-server"
    echo "registering with detected harnesses"
    "$VENV/bin/python" "$REPO/scripts/install_mcp.py" register \
      --command "$VENV/bin/python" --cwd "$REPO/mcp-server" --ask
    echo
    echo "done. server: $VENV/bin/python -m scrape_n_search"
    ;;
  register)
    [[ -x "$VENV/bin/python" ]] || { echo "not installed yet — run ./install.sh first" >&2; exit 1; }
    "$VENV/bin/python" "$REPO/scripts/install_mcp.py" register \
      --command "$VENV/bin/python" --cwd "$REPO/mcp-server" "${@:2}"
    ;;
  list)
    "$VENV/bin/python" "$REPO/scripts/install_mcp.py" list "${@:2}" \
      || "$PYTHON" "$REPO/scripts/install_mcp.py" list "${@:2}"
    ;;
  uninstall)
    if [[ -x "$VENV/bin/python" ]]; then
      echo "unregistering from harnesses:"
      "$VENV/bin/python" "$REPO/scripts/install_mcp.py" unregister "${@:2}" || true
      read -r -p "Remove the dedicated venv at $VENV? [y/N] " reply || reply=
      if [[ "${reply,,}" == "y" ]]; then
        rm -rf "$VENV"
        echo "removed $VENV"
      else
        echo "left $VENV in place"
      fi
    else
      echo "no dedicated venv found at $VENV"
    fi
    ;;
  *)
    echo "unknown mode: $MODE (install|register|list|uninstall)" >&2
    exit 1
    ;;
esac
