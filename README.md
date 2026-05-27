# sendspin_auracast

Receive Sendspin PCM audio and broadcast it over Bluetooth LE Audio
(Auracast) using Bumble.

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

Broadcast audio from a Sendspin WebSocket server:

```bash
sendspin-auracast ws://localhost:8927/sendspin
```

The client advertises itself as a Sendspin player, receives PCM audio chunks,
encodes them as LC3, and broadcasts them as an Auracast stream.

Use a custom Bumble transport or broadcast name:

```bash
sendspin-auracast ws://localhost:8927/sendspin \
  --transport serial:/dev/ttyACM0,1000000 \
  --name "Sendspin Auracast"
```

Broadcast with encryption:

```bash
sendspin-auracast ws://localhost:8927/sendspin --code "my-password"
```

Add manufacturer-specific BLE advertising data:

```bash
sendspin-auracast ws://localhost:8927/sendspin --manufacturer-data 0x0059:010203
```

Tune startup latency:

```bash
sendspin-auracast ws://localhost:8927/sendspin \
  --sendspin-buffer-ms 50 \
  --presentation-delay-us 20000 \
  --max-transport-latency-ms 40
```

The bridge defaults to a 100 ms advertised Sendspin player buffer. Smaller
values can reduce the delay after pressing play, while larger values may be
more reliable on busy hosts or noisy Bluetooth links.

For debugging, print received chunks instead of broadcasting:

```bash
sendspin-auracast ws://localhost:8927/sendspin --print
```

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

Prepare a release locally:

```bash
python scripts/prepare_release.py 0.2.0
```

Publish to PyPI:

```bash
twine check dist/*
twine upload dist/*
```

## Releases

Use the GitHub Actions workflow `Prepare Release` to create a PyPI release. It:

- updates `pyproject.toml` to the requested version
- runs lint, tests, `python -m build`, and `twine check`
- commits the version bump and creates a `v*` tag
- pushes the commit and tag
- creates a GitHub Release

The existing `Publish to PyPI` workflow publishes when the new `v*` tag is
pushed. Make sure the repository's PyPI trusted publishing settings are
configured for the `release` environment before using it.
