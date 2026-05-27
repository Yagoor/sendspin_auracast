from dataclasses import dataclass

import numpy as np

from sendspin_auracast.sendspin_bridge import (
    PcmFrameBuffer,
    PlayerAudioState,
    parse_manufacturer_data,
    pcm_buffer_capacity_bytes,
    pcm_bytes_to_int16,
)


@dataclass(frozen=True)
class FakePCMFormat:
    channels: int
    bit_depth: int


@dataclass(frozen=True)
class FakeAudioFormat:
    pcm_format: FakePCMFormat


def test_pcm_bytes_to_int16_duplicates_mono_to_stereo() -> None:
    converted = pcm_bytes_to_int16(
        np.array([1, -2], dtype="<i2").tobytes(),
        channels=1,
        bit_depth=16,
        target_channels=2,
    )

    np.testing.assert_array_equal(
        converted,
        np.array([[1, 1], [-2, -2]], dtype=np.int16),
    )


def test_pcm_frame_buffer_returns_complete_frames() -> None:
    frame_buffer = PcmFrameBuffer(channels=2, samples_per_frame=2)
    audio_format = FakeAudioFormat(pcm_format=FakePCMFormat(channels=2, bit_depth=16))
    payload = np.array([[1, 2], [3, 4], [5, 6]], dtype="<i2").tobytes()

    frames = frame_buffer.add_chunk(payload, audio_format)

    assert len(frames) == 1
    np.testing.assert_array_equal(
        frames[0],
        np.array([[1, 2], [3, 4]], dtype=np.int16),
    )


def test_pcm_buffer_capacity_bytes_from_milliseconds() -> None:
    assert (
        pcm_buffer_capacity_bytes(
            sample_rate=48_000,
            channels=2,
            bit_depth=16,
            buffer_ms=100,
        )
        == 19_200
    )


def test_parse_manufacturer_data() -> None:
    assert parse_manufacturer_data(["0x0059:010203"]) == {0x0059: b"\x01\x02\x03"}


def test_player_audio_state_scales_volume() -> None:
    samples = np.array([[1000, -1000], [30001, -30001]], dtype=np.int16)

    adjusted = PlayerAudioState(volume=50, muted=False).apply(samples)

    np.testing.assert_array_equal(
        adjusted,
        np.array([[500, -500], [15000, -15000]], dtype=np.int16),
    )


def test_player_audio_state_mute_zeros_samples() -> None:
    samples = np.array([[1000, -1000]], dtype=np.int16)

    adjusted = PlayerAudioState(volume=100, muted=True).apply(samples)

    np.testing.assert_array_equal(adjusted, np.zeros_like(samples))
