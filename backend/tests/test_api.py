from fastapi.testclient import TestClient

from dreampet.api.app import create_app


def _client(runtime, token=None):
    cfg = runtime.ctx.cfg
    cfg.api.admin_token = token
    app = create_app(cfg, runtime=runtime, start_scheduler=False)
    return TestClient(app)


def test_auth_required_when_token_set(runtime):
    with _client(runtime, token="s3cret") as c:
        assert c.get("/api/v1/pets/me/status").status_code == 401
        ok = c.get("/api/v1/pets/me/status", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200
        assert c.get("/api/v1/pets/me/status?token=s3cret").status_code == 200


def test_status_chat_memories_dreams(runtime):
    with _client(runtime) as c:
        st = c.get("/api/v1/pets/t/status").json()
        assert st["pet"]["name"] == "Quark" and st["state"] == "idle"
        runtime.ctx.state.boredom = 0.9
        runtime.run_explore()
        r = c.post("/api/v1/pets/t/chat", json={"message": "hi!", "stream": False}).json()
        assert r["reply"]
        mems = c.get("/api/v1/pets/t/memories", params={"kind": "episodic"}).json()
        assert mems and mems[0]["source_url"]
        one = c.get(f"/api/v1/memories/{mems[0]['id']}").json()
        assert one["id"] == mems[0]["id"] and "links" in one
        # owner can forget a memory
        assert c.delete(f"/api/v1/memories/{mems[-1]['id']}").json()["ok"]
        assert c.get(f"/api/v1/memories/{mems[-1]['id']}").status_code == 404
        assert c.get("/api/v1/pets/t/clusters").json()


def test_chat_streams(runtime):
    with _client(runtime) as c:
        with c.stream("POST", "/api/v1/pets/t/chat", json={"message": "tell me a thing"}) as r:
            body = "".join(r.iter_text())
        assert "event: token" in body and "event: done" in body


def test_persona_and_drives_patch(runtime):
    with _client(runtime) as c:
        p = c.patch("/api/v1/pets/t/persona", json={"traits": {"restlessness": 90}}).json()
        assert p["persona"]["traits"]["restlessness"] == 90
        assert p["compiled"]["drives"]["boredom"]["growth_rate"] > 0.08
        bad = c.patch("/api/v1/pets/t/drives", json={"boredom.growth_rate": 5})
        assert bad.status_code == 422
        good = c.patch("/api/v1/pets/t/drives", json={"boredom.growth_rate": 0.2}).json()
        assert good["params"]["boredom"]["growth_rate"] == 0.2 and good["overrides"]
        cleared = c.patch("/api/v1/pets/t/drives", json={"boredom.growth_rate": None}).json()
        assert "boredom.growth_rate" not in cleared["overrides"]
        y = c.get("/api/v1/pets/t/persona/export").text
        assert "persona:" in y
        assert c.post("/api/v1/pets/t/persona/import", content=y).status_code == 200
        assert c.get("/api/v1/presets").json()


def test_settings_never_expose_keys(runtime):
    runtime.ctx.cfg.roles["chat_llm"] = runtime.ctx.cfg.role("chat_llm").model_copy(update={"api_key": "sk-xyz"})
    with _client(runtime) as c:
        body = c.get("/api/v1/settings").text
        assert "sk-xyz" not in body


def test_clock_speed_is_adjustable_for_sim_pets(runtime):
    with _client(runtime) as c:
        clk = c.get("/api/v1/clock").json()
        assert clk["simulated"] and clk["adjustable"]
        assert c.patch("/api/v1/clock", json={"speed": 0.5}).status_code == 422
        out = c.patch("/api/v1/clock", json={"speed": 600}).json()
        assert out["speed"] == 600 and runtime.ctx.clock.speed == 600
        assert (runtime.ctx.cfg.data_path / "clock_speed.txt").read_text() == "600"


def test_thin_samples_keeps_state_changes_and_last_point():
    from dreampet.api.app import thin_samples

    rows = [{"t": i, "state": "asleep" if 400 <= i < 407 else "idle"} for i in range(1000)]
    out = thin_samples(rows, 100)
    assert len(out) < 150
    ts = {r["t"] for r in out}
    assert {400, 407, 999} <= ts


def test_theme_color_is_part_of_the_persona(runtime):
    with _client(runtime) as c:
        assert c.get("/api/v1/pets/t/status").json()["pet"]["theme_color"] == "#2f6fd6"
        assert c.patch("/api/v1/pets/t/persona", json={"theme_color": "purple"}).status_code == 422
        c.patch("/api/v1/pets/t/persona", json={"theme_color": "#C4553A"})
        assert c.get("/api/v1/pets/t/status").json()["pet"]["theme_color"] == "#C4553A"
        assert "#C4553A" in c.get("/api/v1/pets/t/persona/export").text  # travels with shared personas
