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
# One-line setup (fetches this script from GitHub and runs it non-interactively):
#
#   curl -fsSL https://raw.githubusercontent.com/hughyyy/scrape-n-search/main/install.sh | bash
#
# When run outside a checkout (or with a non-terminal stdin), the script
# downloads the source tarball for REF (default: main; pin tags with
# `-s -- --ref v0.1.0`) into $DATA/src, copies .env.example to .env, and
# registers with harnesses without prompting. Interactive confirmation
# (--ask) is only used when stdin is a terminal.
#
# Registration uses a *dedicated* venv (not the repo's .venv) so the command
# path embedded in harness configs stays stable even if the repo moves.

set -euo pipefail

DATA="${XDG_DATA_HOME:-$HOME/.local/share}/scrape-n-search"
VENV="$DATA/venv"
SRC="$DATA/src"
PYTHON="${PYTHON:-python3}"

# --- args: --ref <ref> is consumed here; everything else is forwarded --------
REF="main"
fwd=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref) REF="${2:?--ref needs a value}"; shift 2 ;;
    --ref=*) REF="${1#*=}"; shift ;;
    *) fwd+=("$1"); shift ;;
  esac
done
MODE="${fwd[0]:-install}"
rest=("${fwd[@]:1}")

# --- source resolution --------------------------------------------------------
# Running from a checkout (or from inside it, which is also where BASH_SOURCE
# resolves when the script comes over a pipe) → use it as-is, preserving the
# local mcp-server/.env. Otherwise fetch the source tarball into $DATA/src.
here="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" 2>/dev/null && pwd || true)"
if [[ -n "$here" && -f "$here/mcp-server/pyproject.toml" ]]; then
  REPO="$here"
  echo "using local checkout: $REPO"
else
  if [[ "$(cat "$DATA/.ref" 2>/dev/null || true)" != "$REF" || ! -f "$SRC/mcp-server/pyproject.toml" ]]; then
    echo "fetching scrape-n-search source (ref: $REF) into $SRC"
    if [[ "$REF" == v* ]]; then
      tar_url="${SNR_TARBALL_URL:-https://codeload.github.com/hughyyy/scrape-n-search/tar.gz/refs/tags/$REF}"
    else
      tar_url="${SNR_TARBALL_URL:-https://codeload.github.com/hughyyy/scrape-n-search/tar.gz/refs/heads/$REF}"
    fi
    tmp="$(mktemp -d)"
    curl -fsSL "$tar_url" | tar -xz -C "$tmp"
    src_dir="$(find "$tmp" -maxdepth 1 -type d -name 'scrape-n-search*' | head -n 1)"
    if [[ -z "$src_dir" || ! -f "$src_dir/mcp-server/pyproject.toml" ]]; then
      echo "downloaded tarball did not contain the scrape-n-search source" >&2
      rm -rf "$tmp"
      exit 1
    fi
    rm -rf "$SRC"
    mkdir -p "$DATA"
    mv "$src_dir" "$SRC"
    rm -rf "$tmp"
    echo "$REF" > "$DATA/.ref"
    cp "$SRC/mcp-server/.env.example" "$SRC/mcp-server/.env"
    echo "source ready; API keys go in $SRC/mcp-server/.env (or run $SRC/setup.sh)"
  fi
  REPO="$SRC"
fi

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
    ask=(--ask)
    [[ -t 0 ]] || ask=()
    "$VENV/bin/python" "$REPO/scripts/install_mcp.py" register \
      --command "$VENV/bin/python" --cwd "$REPO/mcp-server" "${ask[@]}"
    echo
    echo "done. server: $VENV/bin/python -m scrape_n_search"
    echo "configure API keys in $REPO/mcp-server/.env (or run $REPO/setup.sh)"
    ;;
  register)
    [[ -x "$VENV/bin/python" ]] || { echo "not installed yet — run ./install.sh first" >&2; exit 1; }
    "$VENV/bin/python" "$REPO/scripts/install_mcp.py" register \
      --command "$VENV/bin/python" --cwd "$REPO/mcp-server" "${rest[@]}"
    ;;
  list)
    "$VENV/bin/python" "$REPO/scripts/install_mcp.py" list "${rest[@]}" \
      || "$PYTHON" "$REPO/scripts/install_mcp.py" list "${rest[@]}"
    ;;
  uninstall)
    if [[ -x "$VENV/bin/python" ]]; then
      echo "unregistering from harnesses:"
      "$VENV/bin/python" "$REPO/scripts/install_mcp.py" unregister "${rest[@]}" || true
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
