"""Command-line client for receiving Sendspin audio chunks."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from dataclasses import dataclass
from typing import Protocol

DEFAULT_URL = "ws://localhost:8927/sendspin"
DEFAULT_CLIENT_NAME = "sendspin_auracast"
DEFAULT_BROADCAST_NAME = "Sendspin Auracast"
DEFAULT_PREVIEW_BYTES = 32
DEFAULT_BUFFER_CAPACITY = 2 * 1024 * 1024
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
DEFAULT_SENDSPIN_BUFFER_MS = 100
DEFAULT_QUEUE_SIZE = 20
DEFAULT_INITIAL_VOLUME = 100


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
    initial_volume: int


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
        description="Broadcast Sendspin audio over Bluetooth LE Audio."
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
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )
    parser.add_argument(
        "-t",
        "--transport",
        default="usb:0",
        metavar="SPEC",
        help="Bumble transport used for Auracast broadcast. Defaults to usb:0.",
    )
    parser.add_argument(
        "-n",
        "--name",
        default=DEFAULT_BROADCAST_NAME,
        help="Auracast broadcast name visible to receivers.",
    )
    parser.add_argument(
        "-c",
        "--code",
        metavar="PASSWORD",
        help="Optional Auracast encryption password, max 16 characters.",
    )
    parser.add_argument(
        "--broadcast-id",
        type=lambda value: int(value, 0),
        default=0x123456,
        help="Auracast broadcast ID. Accepts decimal or 0x-prefixed hex.",
    )
    parser.add_argument(
        "--bitrate",
        type=int,
        default=80_000,
        help="LC3 bitrate in bits per second per channel.",
    )
    parser.add_argument(
        "--sendspin-buffer-ms",
        type=int,
        default=DEFAULT_SENDSPIN_BUFFER_MS,
        help=(
            "Advertised Sendspin player buffer in milliseconds. "
            f"Defaults to {DEFAULT_SENDSPIN_BUFFER_MS}."
        ),
    )
    parser.add_argument(
        "--queue-size",
        type=int,
        default=DEFAULT_QUEUE_SIZE,
        help=(
            "Maximum local Sendspin chunks queued before stale audio is dropped. "
            f"Defaults to {DEFAULT_QUEUE_SIZE}."
        ),
    )
    parser.add_argument(
        "--presentation-delay-us",
        type=int,
        default=40_000,
        help="Auracast presentation delay in microseconds. Defaults to 40000.",
    )
    parser.add_argument(
        "--max-transport-latency-ms",
        type=int,
        default=65,
        help="Auracast BIG max transport latency in milliseconds. Defaults to 65.",
    )
    parser.add_argument(
        "--manufacturer-data",
        action="append",
        metavar="COMPANY_ID:HEX_DATA",
        help="Manufacturer-specific advertising data. Can be repeated.",
    )
    parser.add_argument(
        "--preview-bytes",
        type=int,
        default=DEFAULT_PREVIEW_BYTES,
        help="Number of audio bytes to show per chunk in --print mode.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Print received audio chunk summaries instead of broadcasting.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Write raw Sendspin audio bytes to stdout instead of broadcasting.",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        help="Seconds to wait for the initial WebSocket connection.",
    )
    parser.add_argument(
        "--initial-volume",
        type=int,
        default=DEFAULT_INITIAL_VOLUME,
        help="Initial Sendspin player volume from 0 to 100. Defaults to 100.",
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
        initial_volume=config.initial_volume,
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
    """Run the Sendspin Auracast CLI."""
    from sendspin_auracast.auracast_broadcaster import BroadcastConfig
    from sendspin_auracast.sendspin_bridge import (
        SendspinAuracastConfig,
        broadcast_until_stopped,
        parse_manufacturer_data,
    )

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.preview_bytes < 0:
        parser.error("--preview-bytes must be zero or greater")
    if args.connect_timeout <= 0:
        parser.error("--connect-timeout must be greater than zero")
    if not 0 <= args.initial_volume <= 100:
        parser.error("--initial-volume must be between 0 and 100")
    if args.bitrate <= 0:
        parser.error("--bitrate must be greater than zero")
    if args.sendspin_buffer_ms <= 0:
        parser.error("--sendspin-buffer-ms must be greater than zero")
    if args.queue_size <= 0:
        parser.error("--queue-size must be greater than zero")
    if args.presentation_delay_us <= 0:
        parser.error("--presentation-delay-us must be greater than zero")
    if args.max_transport_latency_ms <= 0:
        parser.error("--max-transport-latency-ms must be greater than zero")
    if args.code and len(args.code) > 16:
        parser.error("--code must be 16 characters or fewer")
    if args.raw and args.print:
        parser.error("--raw and --print cannot be used together")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(name)s: %(message)s",
    )

    config = ClientConfig(
        url=args.url,
        client_id=args.client_id,
        client_name=args.client_name,
        preview_bytes=args.preview_bytes,
        raw=args.raw,
        connect_timeout=args.connect_timeout,
        initial_volume=args.initial_volume,
    )

    try:
        if args.raw or args.print:
            asyncio.run(run_client(config))
        else:
            manufacturer_data = parse_manufacturer_data(args.manufacturer_data)
            broadcast_config = BroadcastConfig(
                name=args.name,
                broadcast_id=args.broadcast_id,
                bitrate=args.bitrate,
                presentation_delay_us=args.presentation_delay_us,
                max_transport_latency_ms=args.max_transport_latency_ms,
                broadcast_code=(
                    args.code.encode().ljust(16, b"\x00")[:16]
                    if args.code
                    else None
                ),
                manufacturer_data=manufacturer_data,
            )
            asyncio.run(
                broadcast_until_stopped(
                    SendspinAuracastConfig(
                        url=args.url,
                        client_id=args.client_id,
                        client_name=args.client_name,
                        connect_timeout=args.connect_timeout,
                        initial_volume=args.initial_volume,
                        transport_spec=args.transport,
                        broadcast=broadcast_config,
                        sendspin_buffer_ms=args.sendspin_buffer_ms,
                        queue_size=args.queue_size,
                    )
                )
            )
    except ValueError as err:
        print(f"Invalid option: {err}", file=sys.stderr)
        return 2
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
