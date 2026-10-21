#!/usr/bin/env python3
"""Install / register / unregister the scrape-n-search MCP server with harnesses.

stdlib-only registration engine for the interactive setup wizard / install script.

Subcommands
-----------
  list        Show detected harnesses and registration state.
  register    Merge a scrape-n-search entry into harness MCP config(s).
  unregister  Remove the entry again.

Every JSON harness config is merged in place: other servers and top-level keys
are preserved, a timestamped backup is written before any change, and writes
are atomic (tmp + rename). Codex uses a TOML schema that is not yet supported
— it is detected and reported, but never modified.

Registration entries intentionally point at a *python interpreter* with
`args: ["-m", "scrape_n_search"]` and an absolute `cwd` (the server auto-loads
`mcp-server/.env` from its project dir, so no API key is inlined into harness
configs). Set a generous `timeout` where the harness supports it — Scrappey
browser calls can take 20-40s, most harness defaults are ~30s.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

SERVER_ID = "scrape-n-search"
DEFAULT_TIMEOUT_MS = 120000

# container key per harness (VS Code differs; Codex is TOML/unsupported)
CONTAINER = {"omp", "claude-desktop", "claude-code", "cursor", "vscode"}


def _home() -> Path:
    return Path.home()


def repo_root() -> Path:
    # scripts/install_mcp.py -> repo root
    return Path(__file__).resolve().parent.parent


def dedicated_venv() -> Path:
    data = os.environ.get("XDG_DATA_HOME") or str(_home() / ".local/share")
    return Path(data) / "scrape-n-search" / "venv"


def mcp_server_dir(repo: Path) -> Path:
    return repo / "mcp-server"


def default_command(repo: Path) -> str | None:
    """Preferred server interpreter: dedicated install venv, else checkout venv."""
    for cand in (dedicated_venv() / "bin" / "python", mcp_server_dir(repo) / ".venv" / "bin" / "python"):
        if cand.is_file():
            return str(cand)
    return None


@dataclass
class Harness:
    id: str
    name: str
    container: str
    targets: list[str]                 # candidate config paths, home-relative templates
    include_type: bool = False
    include_timeout: bool = False
    binary: str | None = None
    supported: bool = True
    note: str = ""

    def candidate_paths(self, repo: Path, home: Path | None = None) -> list[Path]:
        h = home or _home()
        paths: list[Path] = []
        for t in self.targets:
            if t.startswith("~"):
                paths.append(h / t[2:].lstrip("/"))
            elif t.startswith("{repo}"):
                if repo is not None:
                    paths.append(Path(t.replace("{repo}", str(repo), 1)))
            else:
                paths.append(Path(t))
        return paths

    def installed(self, repo: Path, home: Path | None = None) -> bool:
        if any(p.exists() for p in self.candidate_paths(repo, home)):
            return True
        return bool(self.binary and shutil.which(self.binary))

    def entry(self, command: str, cwd: str | None, timeout_ms: int) -> dict:
        e = {"command": command, "args": ["-m", "scrape_n_search"]}
        if self.include_type:
            e["type"] = "stdio"
        if cwd:
            e["cwd"] = cwd
        if self.include_timeout and timeout_ms:
            e["timeout"] = timeout_ms
        return e


def make_harnesses() -> list[Harness]:
    return [
        Harness(
            "omp", "Oh My Pi", "mcpServers",
            ["~/.omp/agent/mcp.json"],
            include_type=True, include_timeout=True,
            binary="omp",
        ),
        Harness(
            "claude-desktop", "Claude Desktop", "mcpServers",
            _claude_desktop_paths(),  # OS-specific, expanded at call time? no - static templates
            binary="claude",
        ),
        Harness(
            "claude-code", "Claude Code", "mcpServers",
            ["{repo}/.mcp.json", "~/.claude.json"],
            include_type=True,
            binary="claude",
            note="prefers a project .mcp.json; falls back to ~/.claude.json",
        ),
        Harness(
            "cursor", "Cursor", "mcpServers",
            ["{repo}/.cursor/mcp.json", "~/.cursor/mcp.json"],
            include_type=True,
            binary="cursor",
        ),
        Harness(
            "vscode", "VS Code", "servers",
            ["{repo}/.vscode/mcp.json"],
            binary="code",
            note="uses the 'servers' key, not 'mcpServers'",
        ),
        Harness(
            "codex", "Codex", "",
            ["~/.codex/config.toml"],
            binary="codex",
            supported=False,
            note="TOML config schema not yet supported — detected only, never modified",
        ),
    ]


def _claude_desktop_paths() -> list[str]:
    # static templates: expand "~" at use time; OS variants live in the same list
    return [
        "~/.config/Claude/claude_desktop_config.json",
        "~/Library/Application Support/Claude/claude_desktop_config.json",
        "~/AppData/Roaming/Claude/claude_desktop_config.json",
    ]


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    bak = path.with_name(path.name + ".backup")
    shutil.copy2(path, bak)
    return bak


def save_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def pick_target(h: Harness, repo: Path, home: Path | None = None) -> Path | None:
    """First candidate whose parent dir exists (so the file can be created)."""
    for p in h.candidate_paths(repo, home):
        if p.parent.exists():
            return p
    return None


def registered_in(h: Harness, repo: Path, home: Path | None = None) -> Path | None:
    for p in h.candidate_paths(repo, home):
        if p.exists() and SERVER_ID in load_config(p).get(h.container, {}):
            return p
    return None


def register_harness(
    h: Harness,
    command: str,
    cwd: str | None,
    timeout_ms: int,
    repo: Path,
    home: Path | None = None,
    dry_run: bool = False,
    ask: bool = False,
) -> list[str]:
    """Merge our entry into the harness config. Returns human-readable lines."""
    out: list[str] = []
    target = pick_target(h, repo, home)
    if target is None:
        out.append(f"  {h.id}: no config location available (none of the candidate dirs exist)")
        return out
    data = load_config(target)
    container = data.get(h.container)
    if not isinstance(container, dict):
        container = {}
    entry = h.entry(command, cwd, timeout_ms)
    new_container = dict(container)
    prev = new_container.get(SERVER_ID)
    new_container[SERVER_ID] = entry
    new_data = dict(data)
    new_data[h.container] = new_container
    if new_container == container:
        out.append(f"  {h.id}: already registered ({target})")
        return out
    if dry_run:
        out.append(f"  {h.id}: [dry-run] would register {target} -> {json.dumps(entry)}")
        return out
    if ask:
        try:
            reply = input(f"  register {h.id} -> {target}? [y/N] ").strip().lower()
        except EOFError:
            reply = ""  # non-interactive stdin: treat as declined
        if reply != "y":
            out.append(f"  {h.id}: skipped (declined)")
            return out
    bak = backup(target)
    save_config(target, new_data)
    out.append(f"  {h.id}: registered {SERVER_ID} -> {target}")
    if bak:
        out.append(f"       backup: {bak}")
    if prev:
        out.append(f"       replaced previous entry")
    return out


def unregister_harness(
    h: Harness, repo: Path, home: Path | None = None, dry_run: bool = False
) -> list[str]:
    out: list[str] = []
    removed = False
    for target in h.candidate_paths(repo, home):
        if not target.exists():
            continue
        data = load_config(target)
        container = data.get(h.container)
        if not isinstance(container, dict) or SERVER_ID not in container:
            continue
        new_container = {k: v for k, v in container.items() if k != SERVER_ID}
        new_data = dict(data)
        new_data[h.container] = new_container
        if dry_run:
            out.append(f"  {h.id}: [dry-run] would remove {SERVER_ID} from {target}")
        else:
            bak = backup(target)
            save_config(target, new_data)
            out.append(f"  {h.id}: removed {SERVER_ID} from {target}")
            if bak:
                out.append(f"       backup: {bak}")
        removed = True
        break
    if not removed:
        out.append(f"  {h.id}: not registered")
    return out


def _select(ids: list[str] | None, all_harnesses: list[Harness], repo: Path, home: Path | None) -> list[Harness]:
    if ids:
        known = {h.id: h for h in all_harnesses}
        missing = [i for i in ids if i not in known]
        if missing:
            raise SystemExit(f"unknown harness(es): {', '.join(missing)} (known: {', '.join(known)})")
        return [known[i] for i in ids]
    return [h for h in all_harnesses if h.installed(repo, home)]


def cmd_list(args: argparse.Namespace, repo: Path, home: Path | None) -> int:
    command = default_command(repo)
    mdcwd = mcp_server_dir(repo)
    print(f"server: {SERVER_ID}")
    print(f"interpreter: {command or '(none — run install.sh or pass --command)'}")
    print(f"project dir: {mdcwd if mdcwd.is_dir() else '(none)'}\n")
    for h in make_harnesses():
        reg = registered_in(h, repo, home)
        installed = h.installed(repo, home)
        state = "detected, registered" if reg else ("detected, not registered" if installed else "not detected")
        line = f"  {h.id:15s} {h.name:15s} {state}"
        if h.note:
            line += f"  ({h.note})"
        print(line)
        if reg:
            print(f"                    -> {reg}")
    return 0


def cmd_register(args: argparse.Namespace, repo: Path, home: Path | None) -> int:
    command = args.command or default_command(repo)
    if not command:
        print("no server interpreter found — run install.sh (or pass --command)", file=sys.stderr)
        return 1
    if args.cwd:
        cwd = args.cwd
    else:
        md = mcp_server_dir(repo)
        cwd = str(md) if md.is_dir() else None
    sel = _select(args.harness, make_harnesses(), repo, home)
    if not sel:
        print("no harnesses detected — pass --harness to register a specific one")
        return 0
    changed = False
    for h in sel:
        if not h.supported:
            print(f"  {h.id}: {h.note} — skipped")
            continue
        lines = register_harness(h, command, cwd, args.timeout_ms, repo, home, args.dry_run, args.ask)
        for ln in lines:
            print(ln)
            if "[dry-run]" not in ln and "already registered" not in ln:
                changed = True
    print("\nreload the affected harness (e.g. /mcp reload in OMP) to pick up the change.")
    return 0


def cmd_unregister(args: argparse.Namespace, repo: Path, home: Path | None) -> int:
    sel = _select(args.harness, make_harnesses(), repo, home)
    for h in sel:
        for ln in unregister_harness(h, repo, home, args.dry_run):
            print(ln)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", default=None, help="repo root (default: parent of this script)")
    p.add_argument("--home", default=None, help=argparse.SUPPRESS)  # test hook
    sub = p.add_subparsers(dest="cmd", required=True)

    sl = sub.add_parser("list", help="show detected harnesses + registration state")
    sl.set_defaults(fn=cmd_list)

    sr = sub.add_parser("register", help="register with harnesses")
    sr.add_argument("--harness", action="append", help="harness id(s); default: all detected")
    sr.add_argument("--command", default=None, help="server interpreter (absolute python path)")
    sr.add_argument("--cwd", default=None, help="working dir for -m scrape_n_search (default: repo/mcp-server)")
    sr.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    sr.add_argument("--dry-run", action="store_true")
    sr.add_argument("--ask", action="store_true", help="confirm each harness before writing")
    sr.set_defaults(fn=cmd_register)

    su = sub.add_parser("unregister", help="remove registration from harnesses")
    su.add_argument("--harness", action="append")
    su.add_argument("--dry-run", action="store_true")
    su.set_defaults(fn=cmd_unregister)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo) if args.repo else repo_root()
    home = Path(args.home) if args.home else None
    return args.fn(args, repo, home)


if __name__ == "__main__":
    sys.exit(main())
