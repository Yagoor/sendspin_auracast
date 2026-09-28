import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
from aiosendspin.models import PlayerCommand
from aiosendspin.noise import InMemoryClientPairingStore

import sendspin_auracast.sendspin_bridge as sendspin_bridge
from sendspin_auracast.sendspin_bridge import (
    PcmFrameBuffer,
    PlayerAudioState,
    SendspinAuracastConfig,
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


def test_bridge_creates_ephemeral_client_and_publishes_player_state(
    monkeypatch,
) -> None:
    sent_states = []
    identities = []
    pairing_stores = []

    class FakeBroadcaster:
        samples_per_frame = 1

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def start_async(self) -> None:
            pass

        async def stop_async(self) -> None:
            pass

        async def send_audio_async(self, _frame) -> None:
            pass

    class FakeSendspinClient:
        def __init__(
            self,
            identity,
            client_name,
            roles,
            *,
            pairing_store,
            player_support,
            initial_volume,
            state_supported_commands,
        ) -> None:
            identities.append(identity.peer_id)
            pairing_stores.append(pairing_store)
            self.connected = False
            self._command_listeners = []
            self._disconnect_listeners = []

        def add_audio_chunk_listener(self, _listener) -> None:
            pass

        def add_disconnect_listener(self, listener) -> None:
            self._disconnect_listeners.append(listener)

        def add_server_command_listener(self, listener) -> None:
            self._command_listeners.append(listener)

        async def connect(self, _url) -> None:
            self.connected = True
            command = SimpleNamespace(
                player=SimpleNamespace(
                    command=PlayerCommand.VOLUME,
                    volume=50,
                    mute=None,
                )
            )
            for listener in self._command_listeners:
                listener(command)
            await asyncio.sleep(0)
            for listener in self._disconnect_listeners:
                listener()

        async def send_player_state(self, **state) -> None:
            sent_states.append(state)

        async def disconnect(self) -> None:
            self.connected = False

    monkeypatch.setattr(sendspin_bridge, "AuracastBroadcaster", FakeBroadcaster)
    monkeypatch.setattr("aiosendspin.client.SendspinClient", FakeSendspinClient)

    asyncio.run(sendspin_bridge.run_sendspin_auracast(SendspinAuracastConfig()))
    asyncio.run(sendspin_bridge.run_sendspin_auracast(SendspinAuracastConfig()))

    assert sent_states == [
        {"available": True, "volume": 50, "muted": False},
        {"available": True, "volume": 50, "muted": False},
    ]
    assert len(identities) == 2
    assert identities[0] != identities[1]
    assert pairing_stores[0] is not pairing_stores[1]
    assert all(
        isinstance(store, InMemoryClientPairingStore) for store in pairing_stores
    )
    for store in pairing_stores:
        pairing_config = asyncio.run(store.get_pairing_config())
        assert pairing_config.unpaired_access_enabled
        assert not pairing_config.pairing_psk_enabled
        assert not pairing_config.static_pin_enabled
        assert not pairing_config.dynamic_pin_enabled
