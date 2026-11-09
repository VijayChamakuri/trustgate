.PHONY: install real synthetic experiments site readme reproduce test smoke

install:
	uv sync --all-groups

real:
	uv run ea real

synthetic:
	uv run ea synthetic

experiments:
	uv run ea experiments

site:
	uv run ea site

readme:
	uv run ea readme

reproduce:
	uv run ea reproduce

test:
	uv run ruff check . && uv run ruff format --check .
	uv run mypy src
	uv run pytest -q --cov=earned_autonomy --cov-report=term-missing

# CI smoke: real warehouse (download + dbt + tests) and the default synthetic world
smoke:
	uv run ea real --skip-benchmark
	uv run ea synthetic
