# BubuLM

BubuLM is a from-scratch project pairing a web crawler with a language model
pipeline: the `crawler` package gathers text from the web, and the `llm`
package turns that corpus into a trained model.

## Project status

Early scaffolding. The repository currently contains the project structure,
tooling, and CI only — the crawler and LLM packages are empty placeholders.
Features will be added incrementally.

## Requirements

- [uv](https://docs.astral.sh/uv/) for Python and dependency management
- Python 3.12 (installed automatically by `uv`)

## Local setup

```bash
git clone https://github.com/saintsauceee/BubuLM.git
cd BubuLM
uv sync --all-groups
```

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
├── crawler/   # web crawling (empty)
├── llm/       # model training and inference (empty)
├── tests/     # test suite
└── docs/      # design notes (empty)
```

## Contributing

Work happens on feature branches (`feat/…`, `fix/…`, `chore/…`), never directly
on `main`. Open a pull request and merge only once CI is green.
