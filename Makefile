.PHONY: install real synthetic experiments site readme reproduce test smoke

install:
	uv sync --all-groups

real:
	uv run trustgate real

synthetic:
	uv run trustgate synthetic

experiments:
	uv run trustgate experiments

site:
	uv run trustgate site

readme:
	uv run trustgate readme

reproduce:
	uv run trustgate reproduce

test:
	uv run ruff check . && uv run ruff format --check .
	uv run mypy src
	uv run pytest -q --cov=trustgate --cov-report=term-missing

# CI smoke: real warehouse (download + dbt + tests) and the default synthetic world
smoke:
	uv run trustgate real --skip-benchmark
	uv run trustgate synthetic
