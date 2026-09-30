import math
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from dreampet.drives.base import DriveState, Event, Lifecycle, make_drive_model
from dreampet.drives.hybrid import HybridLPBoredom
from dreampet.drives.params import DriveParams, apply_overrides
from dreampet.drives.signals import (
    habituation,
    knn_novelty,
    learnability,
    learning_progress,
    lp_with_prior,
    softmax_sample,
    softmax_scale,
)
from dreampet.persona.compiler import compile_persona
from dreampet.persona.model import Persona, Traits

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def test_boredom_growth_is_exact_leaky_integrator():
    m = HybridLPBoredom(DriveParams())
    s = DriveState(boredom=0.2, lifecycle=Lifecycle.IDLE)
    out = m.update(s, [], timedelta(hours=5))
    g = DriveParams().boredom.growth_rate
    assert out.boredom == pytest.approx(1 - 0.8 * math.exp(-g * 5))
    # stepping in pieces gives the same answer as one big step
    step = s
    for _ in range(10):
        step = m.update(step, [], timedelta(minutes=30))
    assert step.boredom == pytest.approx(out.boredom)


def test_satisfaction_lowers_boredom_and_costs_energy():
    m = HybridLPBoredom(DriveParams())
    s = DriveState(boredom=0.8, energy=50, lifecycle=Lifecycle.EXPLORING)
    e = Event("read", T0, {"novelty": 0.5, "learnability": 0.8, "habituation": 1.0, "energy_cost": 4})
    out = m.update(s, [e], timedelta(0))
    assert out.boredom == pytest.approx(0.8 - 0.3 * 0.5 * 0.8)
    assert out.energy == 46


def test_hysteresis_band():
    m = HybridLPBoredom(DriveParams())
    assert m.wants_to_explore(DriveState(boredom=0.71, energy=50))
    assert not m.wants_to_explore(DriveState(boredom=0.69, energy=50))
    assert not m.should_stop_exploring(DriveState(boredom=0.5, energy=50))  # inside the band: keep going
    assert m.should_stop_exploring(DriveState(boredom=0.29, energy=50))
    assert not m.wants_to_explore(DriveState(boredom=0.9, energy=5))  # too tired


def test_sleep_recovers_energy_and_clamps():
    p = DriveParams()
    m = HybridLPBoredom(p)
    out = m.update(DriveState(energy=10, lifecycle=Lifecycle.ASLEEP), [], timedelta(hours=10))
    assert out.energy == p.energy.max


def test_learning_progress():
    assert learning_progress([0.8, 0.7, 0.4, 0.3], 6) == pytest.approx(0.75 - 0.35)
    assert learning_progress([0.2] * 6, 6) == pytest.approx(0)  # mastered
    assert learning_progress([0.9] * 6, 6) == pytest.approx(0)  # noise
    assert lp_with_prior([], 6) > 0  # unexplored clusters get tried


def test_novelty_habituation_learnability():
    assert knn_novelty([], [], 0.6) == 1.0
    assert knn_novelty([0.2], [0.4], 0.5) == pytest.approx(0.3)
    assert habituation(0.8, 0) == 1 and habituation(0.8, 2) == pytest.approx(0.64)
    assert learnability(0.45, 0.1) > learnability(0.05, 0.1)  # moderate error beats trivial
    assert learnability(0.45, 0.1) > learnability(0.95, 0.1)  # ...and beats noise
    assert learnability(0.45, 0.1) > learnability(0.45, -0.1)  # falling error is rewarded


def test_softmax_sample_respects_scores():
    scores = softmax_scale([0.0, 0.1, 0.3])
    picks = [softmax_sample(scores, 0.1, u / 100) for u in range(100)]
    assert picks.count(2) > 80


def test_params_bounds():
    with pytest.raises(ValidationError):
        DriveParams.model_validate({"boredom": {"growth_rate": 0.9}})
    with pytest.raises(ValidationError):
        DriveParams.model_validate({"boredom": {"theta_high": 0.4, "theta_low": 0.35}})
    p = apply_overrides(DriveParams(), {"curiosity.epsilon": 0.2})
    assert p.curiosity.epsilon == 0.2


def test_persona_compiles_within_bounds():
    for v in (0, 50, 100):
        traits = Traits(restlessness=v, openness=v, depth=v, sociability=v, stamina=v, dreaminess=v)
        c = compile_persona(Persona(traits=traits), DriveParams())
        DriveParams.model_validate(c.drives.model_dump())  # always valid
        assert 1 <= c.walk_hops <= 4
    restless = compile_persona(Persona(traits=Traits(restlessness=100)), DriveParams())
    calm = compile_persona(Persona(traits=Traits(restlessness=0)), DriveParams())
    assert restless.drives.boredom.growth_rate > calm.drives.boredom.growth_rate
    over = compile_persona(Persona(), DriveParams(), {"boredom.growth_rate": 0.3})
    assert over.drives.boredom.growth_rate == 0.3 and over.trace["boredom.growth_rate"] == "override"


def test_drive_models_register_via_entry_points():
    assert make_drive_model("hybrid_lp", DriveParams()).name == "hybrid_lp"
    assert make_drive_model("count_based", DriveParams()).name == "count_based"


def test_sim_clock_picks_up_speed_changes_mid_wait():
    import threading
    import time

    from dreampet.clock import SimClock

    clk = SimClock(T0, speed=1.0)  # at 1x, a 10-minute wait would take 10 real minutes
    threading.Timer(0.3, lambda: setattr(clk, "speed", 100_000.0)).start()
    started = time.monotonic()
    clk.sleep_until(T0 + timedelta(minutes=10))
    assert time.monotonic() - started < 2.0
    assert clk.now() == T0 + timedelta(minutes=10)
