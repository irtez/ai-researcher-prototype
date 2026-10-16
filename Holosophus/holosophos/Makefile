.PHONY: install black validate test

install:
	uv pip install -e .[dev]

black:
	uv run black holosophos tests --line-length 100

validate:
	uv run black --check holosophos tests --line-length 100
	uv run flake8 holosophos tests
	uv run mypy holosophos tests --strict --explicit-package-bases

test:
	uv run pytest -s
