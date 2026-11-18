"""The template must pass the repo's own fail-closed preflight (Foundry-managed stores not bypassed)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
PREFLIGHT = ROOT / ".claude/skills/foundry-agent/hosted/assets/preflight.py"
MAIN = Path(__file__).resolve().parents[1] / "main.py"


def _load():
    spec = importlib.util.spec_from_file_location("preflight", PREFLIGHT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_main_passes_preflight():
    status, reason = _load().check_session_manager(MAIN.read_text(encoding="utf-8"))
    assert status == "enabled", reason
