"""Tests for the harness-registration engine (scripts/install_mcp.py)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))

import install_mcp  # noqa: E402  (import after path setup)


@pytest.fixture
def harnesses():
    return {h.id: h for h in install_mcp.make_harnesses()}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point the engine at a throwaway data dir so nothing touches the real home."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


def _mk(target: Path, data: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_default_command_none_without_venv(tmp_path):
    fake_repo = tmp_path / "repo"
    (fake_repo / "mcp-server").mkdir(parents=True)
    assert install_mcp.default_command(fake_repo) is None


def test_default_command_prefers_dedicated_venv(tmp_path):
    fake_repo = tmp_path / "repo"
    (fake_repo / "mcp-server").mkdir(parents=True)
    venv_py = install_mcp.dedicated_venv() / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("#", encoding="utf-8")
    assert install_mcp.default_command(fake_repo) == str(venv_py)


def test_entry_shape_differs_by_harness(harnesses):
    omp = harnesses["omp"].entry("/x/python", "/x/mcp-server", 120000)
    assert omp == {
        "command": "/x/python",
        "args": ["-m", "scrape_n_search"],
        "type": "stdio",
        "cwd": "/x/mcp-server",
        "timeout": 120000,
    }
    desktop = harnesses["claude-desktop"].entry("/x/python", "/x/mcp-server", 120000)
    assert desktop == {"command": "/x/python", "args": ["-m", "scrape_n_search"], "cwd": "/x/mcp-server"}


def test_register_merges_and_preserves_others(tmp_path, monkeypatch, harnesses):
    home = tmp_path / "home"
    target = home / ".omp" / "agent" / "mcp.json"
    _mk(
        target,
        {
            "mcpServers": {"other": {"command": "/a", "args": []}},
            "someTopLevel": "keep",
        },
    )
    h = harnesses["omp"]
    lines = install_mcp.register_harness(
        h, "/venv/bin/python", "/repo/mcp-server", 120000,
        install_mcp.repo_root(), home=home,
    )
    assert any("registered" in ln for ln in lines)
    data = json.loads(target.read_text(encoding="utf-8"))
    # ours merged in
    assert data["mcpServers"]["scrape-n-search"]["command"] == "/venv/bin/python"
    assert data["mcpServers"]["scrape-n-search"]["timeout"] == 120000
    # nothing else clobbered
    assert data["mcpServers"]["other"] == {"command": "/a", "args": []}
    assert data["someTopLevel"] == "keep"
    assert target.with_name(target.name + ".backup").exists()


def test_ask_declines_on_noninteractive_stdin(tmp_path, monkeypatch, harnesses):
    """--ask on a piped stdin (curl | bash) must degrade to 'declined', not crash."""
    home = tmp_path / "home"
    target = home / ".omp" / "agent" / "mcp.json"
    _mk(target, {"mcpServers": {}})

    def _eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    h = harnesses["omp"]
    lines = install_mcp.register_harness(
        h, "/venv/bin/python", "/repo/mcp-server", 120000,
        install_mcp.repo_root(), home=home, ask=True,
    )
    assert any("skipped (declined)" in ln for ln in lines)
    # nothing written, no backup
    assert json.loads(target.read_text(encoding="utf-8"))["mcpServers"] == {}
    assert not target.with_name(target.name + ".backup").exists()


def test_register_is_idempotent(tmp_path, harnesses):
    home = tmp_path / "home"
    target = home / ".omp" / "agent" / "mcp.json"
    _mk(target, {"mcpServers": {}})
    h = harnesses["omp"]
    install_mcp.register_harness(h, "/v", None, 120000, install_mcp.repo_root(), home=home)
    before = target.read_text(encoding="utf-8")
    lines = install_mcp.register_harness(h, "/v", None, 120000, install_mcp.repo_root(), home=home)
    assert any("already registered" in ln for ln in lines)
    assert target.read_text(encoding="utf-8") == before


def test_register_dry_run_writes_nothing(tmp_path, harnesses):
    home = tmp_path / "home"
    target = home / ".omp" / "agent" / "mcp.json"
    _mk(target, {"mcpServers": {}})
    h = harnesses["omp"]
    lines = install_mcp.register_harness(
        h, "/v", None, 120000, install_mcp.repo_root(), home=home, dry_run=True
    )
    assert any("[dry-run]" in ln for ln in lines)
    assert "scrape-n-search" not in target.read_text(encoding="utf-8")
    assert not target.with_name(target.name + ".backup").exists()


def test_unregister_removes_only_ours(tmp_path, harnesses):
    home = tmp_path / "home"
    target = home / ".omp" / "agent" / "mcp.json"
    _mk(target, {"mcpServers": {"scrape-n-search": {"command": "/v"}, "other": {"command": "/a"}}})
    h = harnesses["omp"]
    install_mcp.unregister_harness(h, install_mcp.repo_root(), home=home)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert "scrape-n-search" not in data["mcpServers"]
    assert data["mcpServers"]["other"] == {"command": "/a"}


def test_vscode_uses_servers_container(tmp_path, harnesses):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    target = repo / ".vscode" / "mcp.json"
    _mk(target, {"servers": {"existing": {"command": "/e"}}})
    h = harnesses["vscode"]
    install_mcp.register_harness(h, "/v", "/repo/mcp-server", 120000, repo, home=home)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert "scrape-n-search" in data["servers"]
    assert data["servers"]["existing"] == {"command": "/e"}
    assert "timeout" not in data["servers"]["scrape-n-search"]


def test_codex_is_detect_only(harnesses):
    assert harnesses["codex"].supported is False


def test_unknown_harness_rejected():
    with pytest.raises(SystemExit):
        install_mcp._select(["nope"], install_mcp.make_harnesses(), None, None)


def test_candidate_paths_expand_repo_placeholder(tmp_path):
    h = install_mcp.make_harnesses()[2]  # claude-code
    repo = tmp_path / "repo"
    paths = h.candidate_paths(repo, home=tmp_path / "home")
    assert paths[0] == repo / ".mcp.json"
    assert str(paths[1]).endswith("/.claude.json")


# --- unreadable and non-JSON configs ---------------------------------------


def test_load_config_rejects_malformed_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"a": 1', encoding="utf-8")
    with pytest.raises(install_mcp.ConfigError, match="cannot read"):
        install_mcp.load_config(path)


def test_load_config_rejects_non_object_root(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(install_mcp.ConfigError, match="unexpected config shape"):
        install_mcp.load_config(path)


def test_codex_toml_is_detection_only(tmp_path, harnesses):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text('[mcp_servers.x]\ncommand = "y"\n', encoding="utf-8")
    assert install_mcp.registered_in(harnesses["codex"], tmp_path, home) is None


def test_list_and_unregister_survive_codex_toml(tmp_path, harnesses, capsys):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text('[mcp_servers.x]\ncommand = "y"\n', encoding="utf-8")
    assert install_mcp.cmd_list(argparse.Namespace(), tmp_path, home) == 0
    assert install_mcp.cmd_unregister(
        argparse.Namespace(harness=["codex"], dry_run=True), tmp_path, home
    ) == 0
    out = capsys.readouterr().out
    assert "detection only" in out
    assert "skipped" in out


def test_register_skips_unreadable_config(tmp_path, harnesses, capsys):
    home = tmp_path / "home"
    (home / ".omp" / "agent").mkdir(parents=True)
    target = home / ".omp" / "agent" / "mcp.json"
    target.write_text("{not json", encoding="utf-8")
    lines = install_mcp.register_harness(
        harnesses["omp"], "/x/python", "/x/mcp-server", 1000, tmp_path, home
    )
    assert any("skipped" in line and "cannot read" in line for line in lines)
    assert target.read_text(encoding="utf-8") == "{not json"  # untouched


def test_unregister_removes_every_registered_location(tmp_path, harnesses):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    project = repo / ".mcp.json"
    user = home / ".claude.json"
    entry = {"mcpServers": {install_mcp.SERVER_ID: {"command": "x"}, "other": {"command": "y"}}}
    for path in (project, user):
        _mk(path, entry)
    lines = install_mcp.unregister_harness(harnesses["claude-code"], repo, home)
    assert sum("removed" in line for line in lines) == 2
    for path in (project, user):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert install_mcp.SERVER_ID not in data["mcpServers"]
        assert data["mcpServers"]["other"] == {"command": "y"}


def test_main_returns_nonzero_on_config_error(monkeypatch):
    def boom(args, repo, home):
        raise install_mcp.ConfigError("bad config")

    monkeypatch.setattr(install_mcp, "cmd_list", boom)
    assert install_mcp.main(["list"]) == 1
