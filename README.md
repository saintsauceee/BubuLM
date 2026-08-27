# BubuLM

BubuLM is a from-scratch project pairing a web crawler with a language model
pipeline: the `crawler` package gathers text from the web, and the `llm`
package turns that corpus into a trained model.

## Project status

Early development. The single-worker crawler is implemented: it fetches a URL
and produces a clean, validated training document. The `llm` package is still
an empty placeholder. Features are added incrementally.

## Requirements

- [uv](https://docs.astral.sh/uv/) for Python and dependency management
- Python 3.12 (installed automatically by `uv`)

## Local setup

```bash
git clone https://github.com/saintsauceee/BubuLM.git
cd BubuLM
uv sync --all-groups
```

## Running the crawler

```bash
uv run python -m crawler https://example.com
```

See [docs/crawler.md](docs/crawler.md) for options, configuration, and library
usage.

## Running tests

```bash
uv run pytest
```

## Linting and type checks

```bash
uv run ruff check .        # lint
uv run ruff format .       # format
uv run pyright             # static type check
```

## Repository layout

```text
bubulm/
├── crawler/   # single-worker crawl pipeline
├── llm/       # model training and inference (empty)
├── tests/     # test suite
└── docs/      # component documentation
```
