# BubuLM

BubuLM is a from-scratch project pairing a web crawler with a language model
pipeline: the `crawler` package gathers text from the web, and the `llm`
package turns that corpus into a trained model.

## Project status

Early development. The crawler works on a single machine: it walks a site from
seed URLs concurrently, stores raw HTML on disk, and can turn any single page
into a clean, validated training document. There is no corpus format and no
distribution yet, and the `llm` package is still an empty placeholder.

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

Fetch and clean a single page:

```bash
uv run python -m crawler fetch https://example.com
```

Crawl outward from a seed, storing raw HTML on disk:

```bash
uv run python -m crawler crawl https://example.com --output ./raw
```

See [docs/crawler.md](docs/crawler.md) for the single-page pipeline and
[docs/crawling.md](docs/crawling.md) for the crawl loop.

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
├── crawler/   # fetch pipeline + single-machine crawl loop
├── llm/       # model training and inference (empty)
├── tests/     # test suite
└── docs/      # component documentation
```
