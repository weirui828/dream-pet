"""Scenario tests. The bundled scenarios run in CI on every PR."""

from pathlib import Path

import pytest

from dreampet.config import repo_root
from dreampet.sim.runner import run_scenario
from dreampet.sim.scenario import Scenario, load_scenario

SCENARIOS = sorted((repo_root() / "scenarios").glob("*.yaml"))
VOLATILE = {"run_id", "start", "end"}


def _stable(summary):
    return {k: v for k, v in summary.items() if k not in VOLATILE}


def test_same_seed_same_life(cfg):
    scn = Scenario(name="det", seed=3, persona="presets/naturalist.yaml", days=2,
                   events=[{"day": 1, "time": "15:00", "chat": "hello there"}])
    a = run_scenario(scn, cfg, register=False)
    b = run_scenario(scn, cfg, register=False)
    assert _stable(a.summary) == _stable(b.summary)
    assert a.chats == b.chats
    c = run_scenario(scn.model_copy(update={"seed": 4}), cfg, register=False)
    assert _stable(c.summary) != _stable(a.summary)


def test_injected_articles_show_up(cfg):
    scn = Scenario(name="inject", seed=1, persona="presets/historian.yaml", days=1,
                   events=[{"day": 1, "time": "all", "inject_articles": "corpus/octopus/*"}])
    r = run_scenario(scn, cfg, register=False)
    assert any("octop" in t["label"].lower() for t in r.summary["top_topics"]), r.summary["top_topics"]


@pytest.mark.parametrize("path", SCENARIOS, ids=[p.stem for p in SCENARIOS])
def test_bundled_scenarios_pass(path: Path, cfg):
    scn, base = load_scenario(path)
    r = run_scenario(scn, cfg, base_dir=base, register=False)
    failed = [a for a in r.assertions if not a["ok"]]
    assert not failed, failed
