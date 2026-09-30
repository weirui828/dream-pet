# Contributing

Thanks for helping! Dream Pet is AGPL-3.0. Before your first pull request is merged you'll be
asked to sign a Contributor License Agreement (CLA Assistant comments on the PR). The CLA keeps
dual licensing (AGPL + commercial) possible later.

## Dev setup

```bash
cd backend && uv sync && uv run pytest -q         # backend + scenarios
cd frontend && npm install && npm run dev         # web UI on :3000
uv run --directory backend dreampet demo --speed 600   # a live demo pet on a fast clock, free
```

## Ground rules

- **No wall time.** Everything reads time from `dreampet.clock` (ruff `TID251` and a test enforce it).
  This is what makes simulations reproducible.
- **LLMs think, the scheduler decides.** State transitions, budgets and tool access are code.
  No node gives an LLM arbitrary tool calls; fetched web text only reaches prompts as quoted data.
- **Every provider call goes through a role** (`dreampet/providers/roles.py`) so it is metered,
  capped, and recordable. Every role needs a deterministic `fake`.
- **Golden outputs** live in `backend/tests/golden/`. If you change behaviour on purpose, run
  `UPDATE_GOLDEN=1 uv run pytest tests/test_graphs.py` and commit the diff.
- New scenarios in `scenarios/` run in CI with their assertions — add one when you add a feature.

## Plug-ins without forking

- **Drive models**: implement the `DriveModel` protocol (`dreampet/drives/base.py`) and register an
  entry point in the `dreampet.drive_models` group, then set `drive_model: <name>` in `dreampet.yaml`.
- **Provider adapters**: register in the `dreampet.providers` group with a name like
  `search.myengine`, `fetch.myreader` or `video.myhost`. The factory receives the role's
  `RoleConfig` (extra YAML keys are available via `rc.extra_options()`).
