"""HWFLOW_ROUTER overrides board.router of every design, enters the cache key, and never falls back silently."""
import pytest
import yaml

from hwflow.core.design import Design
from hwflow.core.settings import get_settings
from hwflow.layout.route import krt
from hwflow.pipeline import stages


def _design(tmp_path, board):
    p = tmp_path / "design.yaml"
    p.write_text(yaml.safe_dump({"project": "t", "board": board, "components": {}}))
    return Design.load(p)


def test_env_overrides_board_router(tmp_path, monkeypatch):
    monkeypatch.delenv("HWFLOW_ROUTER", raising=False)
    assert stages.board_router(_design(tmp_path, {})) == "krt"  # the default is unchanged
    assert stages.board_router(_design(tmp_path, {"router": "freerouting"})) == "freerouting"
    monkeypatch.setenv("HWFLOW_ROUTER", "Freerouting")
    assert stages.board_router(_design(tmp_path, {"router": "krt"})) == "freerouting"
    monkeypatch.setenv("HWFLOW_ROUTER", "krt")
    assert stages.board_router(_design(tmp_path, {"router": "freerouting"})) == "krt"


def test_override_is_a_cache_key_input(monkeypatch):
    monkeypatch.delenv("HWFLOW_ROUTER", raising=False)
    a = get_settings().key_dump()
    monkeypatch.setenv("HWFLOW_ROUTER", "freerouting")
    assert get_settings().key_dump() != a


def test_bad_value_is_rejected(monkeypatch):
    monkeypatch.setenv("HWFLOW_ROUTER", "maze")
    with pytest.raises(ValueError):
        get_settings()


def test_router_used_and_no_silent_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("HWFLOW_ROUTER", "krt")
    monkeypatch.setenv("KRT_DIR", str(tmp_path / "absent"))
    d = _design(tmp_path, {})
    assert stages.router_used(d) == "freerouting"  # what the unforced fallback would run
    with pytest.raises(RuntimeError, match="HWFLOW_ROUTER=krt"):
        stages._route_once(d, None, {}, passes=1, out=tmp_path)
    monkeypatch.setenv("HWFLOW_ROUTER", "freerouting")
    monkeypatch.setenv("FREEROUTING", str(tmp_path / "absent.jar"))
    with pytest.raises(FileNotFoundError):
        krt.run_freerouting(tmp_path / "t.dsn", tmp_path / "t.ses")
