# sendspin_auracast

A simple terminal client for receiving Sendspin audio chunks.

## Installation

```bash
pip install sendspin_auracast
```

For local development:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Usage

Run the terminal client against a Sendspin WebSocket server:

```bash
sendspin-auracast ws://localhost:8927/sendspin
```

The client advertises itself as a Sendspin player, receives PCM audio chunks,
and prints each chunk with its timestamp, byte length, format, and a short hex
preview.

To write raw audio bytes to stdout instead:

```bash
sendspin-auracast ws://localhost:8927/sendspin --raw
```

## Development

Run tests:

```bash
pytest
```

Run linting:

```bash
ruff check .
```

Build the package:

```bash
python -m build
```

Publish to PyPI:

```bash
twine upload dist/*
```
