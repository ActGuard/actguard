# Repository Guidelines

## Project Structure & Module Organization
This repository is a small monorepo under `libs/`:
- `sdk-py/`: active Python SDK package (`actguard`) and tests.
- `sdk-js/`: reserved for a JavaScript SDK (currently empty).

Inside `sdk-py/`:
- `actguard/`: library source code.
- `actguard/core/`: pricing/state internals.
- `actguard/integrations/`: provider adapters (`openai`, `anthropic`, `google`).
- `actguard/data/`: static pricing data (`pricing.json`).
- `tests/`: `pytest` test suite.

## Build, Test, and Development Commands
Run commands from `sdk-py/` unless noted.
- `pip install -e ".[dev]"`: install package in editable mode plus dev tools.
- `pytest`: run all tests configured in `pyproject.toml`.
- `ruff check .`: run lint checks (imports, pyflakes, style errors).
- `ruff format .`: format code to project style.

If you use `uv`, keep `uv.lock` in sync when dependency changes are intentional.

## Coding Style & Naming Conventions
- Python 3.9+ target, 4-space indentation, max line length 88 (`ruff`).
- Prefer explicit, typed-friendly APIs and small functions in `actguard/*`.
- Module/file names: `snake_case.py`.
- Test files: `test_*.py` for suite tests; keep one-off local experiments out of committed test files.
- Keep integration logic provider-scoped in `actguard/integrations/<provider>.py`.

## Testing Guidelines
- Framework: `pytest` with `pytest-asyncio` (`asyncio_mode = auto`).
- Add/extend tests in `sdk-py/tests/` for every behavior change.
- Cover both sync and async paths when touching integrations.
- Prefer deterministic unit tests with mocks over live API calls.

## Commit & Pull Request Guidelines
Git history is minimal (`Initial commit`), so use clear conventional-style subjects going forward, e.g.:
- `feat(sdk-py): add token limit guard for streaming`
- `fix(integrations): handle missing usage payload`

PRs should include:
- concise description of behavior change,
- linked issue/task (if available),
- test evidence (`pytest`, `ruff check .`),
- notes on data/schema changes (for `actguard/data/pricing.json`).

## Security & Configuration Tips
- Never commit real API keys or secrets.
- Use environment variables for credentials in local runs.
- Treat pricing data updates as sensitive: include source and date in PR notes.
