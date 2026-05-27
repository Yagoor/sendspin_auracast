from dataclasses import dataclass

from sendspin_auracast.client import build_parser, format_audio_chunk


@dataclass(frozen=True)
class FakeCodec:
    value: str


@dataclass(frozen=True)
class FakePCMFormat:
    sample_rate: int
    channels: int
    bit_depth: int


@dataclass(frozen=True)
class FakeAudioFormat:
    codec: FakeCodec
    pcm_format: FakePCMFormat


def test_format_audio_chunk() -> None:
    formatted = format_audio_chunk(
        123_456,
        b"\x00\x01\x02\x03",
        FakeAudioFormat(
            codec=FakeCodec("pcm"),
            pcm_format=FakePCMFormat(sample_rate=48_000, channels=2, bit_depth=16),
        ),
        preview_bytes=2,
    )

    assert formatted == (
        "timestamp_us=123456 bytes=4 codec=pcm pcm=48000Hz/2ch/16bit "
        "data=00 01 ..."
    )


def test_build_parser_accepts_latency_options() -> None:
    args = build_parser().parse_args(
        [
            "--sendspin-buffer-ms",
            "50",
            "--queue-size",
            "8",
            "--presentation-delay-us",
            "20000",
            "--max-transport-latency-ms",
            "40",
            "--initial-volume",
            "35",
        ]
    )

    assert args.sendspin_buffer_ms == 50
    assert args.queue_size == 8
    assert args.presentation_delay_us == 20_000
    assert args.max_transport_latency_ms == 40
    assert args.initial_volume == 35
