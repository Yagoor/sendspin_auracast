from dataclasses import dataclass

from sendspin_auracast.client import format_audio_chunk


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
