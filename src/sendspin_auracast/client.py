"""Command-line client for receiving Sendspin audio chunks."""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass
from typing import Protocol

DEFAULT_URL = "ws://localhost:8927/sendspin"
DEFAULT_CLIENT_NAME = "sendspin_auracast"
DEFAULT_PREVIEW_BYTES = 32
DEFAULT_BUFFER_CAPACITY = 2 * 1024 * 1024
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0


class PCMFormatLike(Protocol):
    """Shape of the PCM format object provided by aiosendspin."""

    sample_rate: int
    channels: int
    bit_depth: int


class AudioFormatLike(Protocol):
    """Shape of the audio format object provided by aiosendspin."""

    codec: object
    pcm_format: PCMFormatLike


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """Configuration for the terminal audio receiver."""

    url: str
    client_id: str
    client_name: str
    preview_bytes: int
    raw: bool
    connect_timeout: float


def format_audio_chunk(
    timestamp_us: int,
    audio_data: bytes,
    audio_format: AudioFormatLike,
    *,
    preview_bytes: int = DEFAULT_PREVIEW_BYTES,
) -> str:
    """Format one received audio chunk for terminal output."""
    pcm_format = audio_format.pcm_format
    codec = getattr(audio_format.codec, "value", str(audio_format.codec))
    preview = audio_data[:preview_bytes].hex(" ")
    suffix = " ..." if len(audio_data) > preview_bytes else ""
    return (
        f"timestamp_us={timestamp_us} "
        f"bytes={len(audio_data)} "
        f"codec={codec} "
        f"pcm={pcm_format.sample_rate}Hz/"
        f"{pcm_format.channels}ch/"
        f"{pcm_format.bit_depth}bit "
        f"data={preview}{suffix}"
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="Receive Sendspin audio data and print it to the terminal."
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=DEFAULT_URL,
        help=f"Sendspin WebSocket URL. Defaults to {DEFAULT_URL}.",
    )
    parser.add_argument(
        "--client-id",
        default=f"sendspin-auracast-{uuid.getnode():x}",
        help="Stable client identifier to advertise to the Sendspin server.",
    )
    parser.add_argument(
        "--client-name",
        default=DEFAULT_CLIENT_NAME,
        help="Friendly client name to advertise to the Sendspin server.",
    )
    parser.add_argument(
        "--preview-bytes",
        type=int,
        default=DEFAULT_PREVIEW_BYTES,
        help="Number of audio bytes to show per chunk when printing text.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Write raw audio bytes to stdout instead of text summaries.",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        help="Seconds to wait for the initial WebSocket connection.",
    )
    return parser


async def run_client(config: ClientConfig) -> None:
    """Connect to Sendspin and print received audio chunks."""
    from aiosendspin.client import SendspinClient
    from aiosendspin.models import AudioCodec, PlayerCommand, Roles
    from aiosendspin.models.player import (
        ClientHelloPlayerSupport,
        SupportedAudioFormat,
    )

    disconnected = asyncio.Event()

    player_support = ClientHelloPlayerSupport(
        supported_formats=[
            SupportedAudioFormat(
                codec=AudioCodec.PCM,
                channels=2,
                sample_rate=48_000,
                bit_depth=16,
            )
        ],
        buffer_capacity=DEFAULT_BUFFER_CAPACITY,
        supported_commands=[PlayerCommand.VOLUME, PlayerCommand.MUTE],
    )

    client = SendspinClient(
        client_id=config.client_id,
        client_name=config.client_name,
        roles=[Roles.PLAYER],
        player_support=player_support,
        state_supported_commands=[PlayerCommand.SET_STATIC_DELAY],
    )

    def handle_audio_chunk(
        timestamp_us: int,
        audio_data: bytes,
        audio_format: AudioFormatLike,
    ) -> None:
        if config.raw:
            sys.stdout.buffer.write(audio_data)
            sys.stdout.buffer.flush()
            return

        print(
            format_audio_chunk(
                timestamp_us,
                audio_data,
                audio_format,
                preview_bytes=config.preview_bytes,
            ),
            flush=True,
        )

    client.add_audio_chunk_listener(handle_audio_chunk)
    client.add_disconnect_listener(disconnected.set)

    try:
        print(f"Connecting to {config.url} as {config.client_name}...", file=sys.stderr)
        await asyncio.wait_for(client.connect(config.url), config.connect_timeout)
        print(
            "Connected. Waiting for audio chunks. Press Ctrl+C to stop.",
            file=sys.stderr,
        )
        await disconnected.wait()
    finally:
        await client.disconnect()


def main(argv: list[str] | None = None) -> int:
    """Run the terminal client."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.preview_bytes < 0:
        parser.error("--preview-bytes must be zero or greater")
    if args.connect_timeout <= 0:
        parser.error("--connect-timeout must be greater than zero")

    config = ClientConfig(
        url=args.url,
        client_id=args.client_id,
        client_name=args.client_name,
        preview_bytes=args.preview_bytes,
        raw=args.raw,
        connect_timeout=args.connect_timeout,
    )

    try:
        asyncio.run(run_client(config))
    except TimeoutError:
        print(
            f"Connection timed out after {config.connect_timeout:g} seconds.",
            file=sys.stderr,
        )
        return 1
    except OSError as err:
        print(f"Connection failed: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
