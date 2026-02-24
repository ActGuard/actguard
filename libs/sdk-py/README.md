# actguard Python SDK

## Installation

```bash
pip install actguard
```

## SDK Compatibility

The low-level monkey patches in `actguard.integrations` currently support these
minimum SDK versions:

- OpenAI Python SDK: `openai>=1.76.0`
- Google GenAI SDK: `google-genai>=0.8.0`
- Anthropic Python SDK: `anthropic>=0.83.0`

OpenAI minimum is also enforced by a runtime warning in
`actguard/integrations/openai.py`.

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check .
ruff format .
```
