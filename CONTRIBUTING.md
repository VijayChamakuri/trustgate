# Contributing

```bash
uv sync --all-groups
pre-commit install
make test        # ruff, mypy, pytest
```

- Numbers in `README.md` and `docs/RESULTS.md` come from `results/`. Edit `docs/README.template.md` and `docs/RESULTS.template.md`, then run `uv run trustgate readme`. CI fails if either page diverges from a fresh render.
- New warehouse logic belongs in dbt with tests, not in Python.
- Changes that alter results need `make reproduce` and the regenerated `results/` in the same PR.
