# Contributing

```bash
uv sync --all-groups
pre-commit install
make test        # ruff, mypy, pytest
```

- Numbers in `README.md` come from `results/`. Edit `docs/README.template.md` and run `uv run ea readme`; never edit numbers by hand. CI fails if the README and results diverge.
- New warehouse logic belongs in dbt with tests, not in Python.
- Changes that alter results need `make reproduce` and the regenerated `results/` in the same PR.
