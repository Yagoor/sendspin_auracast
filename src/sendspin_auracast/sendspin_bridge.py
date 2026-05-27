"""Bridge Sendspin PCM audio to an Auracast broadcast."""

from __future__ import annotations

import asyncio
import logging
import signal
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from sendspin_auracast.auracast_broadcaster import (
    AuracastBroadcaster,
    BroadcastConfig,
)
from sendspin_auracast.client import (
    DEFAULT_CLIENT_NAME,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_INITIAL_VOLUME,
    DEFAULT_QUEUE_SIZE,
    DEFAULT_SENDSPIN_BUFFER_MS,
    DEFAULT_URL,
    AudioFormatLike,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SendspinAuracastConfig:
    """Configuration for the Sendspin-to-Auracast bridge."""

    url: str = DEFAULT_URL
    client_id: str = f"sendspin-auracast-{uuid.getnode():x}"
    client_name: str = DEFAULT_CLIENT_NAME
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    transport_spec: str = "usb:0"
    broadcast: BroadcastConfig = field(default_factory=BroadcastConfig)
    initial_volume: int = DEFAULT_INITIAL_VOLUME
    sendspin_buffer_ms: int = DEFAULT_SENDSPIN_BUFFER_MS
    queue_size: int = DEFAULT_QUEUE_SIZE


@dataclass(slots=True)
class PlayerAudioState:
    """Local player volume and mute state mirrored from Sendspin commands."""

    volume: int = 100
    muted: bool = False

    def apply(self, samples: np.ndarray) -> np.ndarray:
        """Apply mute/volume to PCM samples without mutating the input frame."""
        if samples.dtype != np.int16:
            raise ValueError("samples must use int16 PCM")
        if self.muted:
            return np.zeros_like(samples)
        if self.volume >= 100:
            return samples.astype(np.int16, copy=True)
        if self.volume <= 0:
            return np.zeros_like(samples)

        scaled = np.rint(samples.astype(np.float32) * (self.volume / 100.0))
        return np.clip(scaled, -32768, 32767).astype(np.int16)


class PcmFrameBuffer:
    """Convert Sendspin PCM bytes into fixed-size int16 sample frames."""

    def __init__(self, channels: int, samples_per_frame: int) -> None:
        self.channels = channels
        self.samples_per_frame = samples_per_frame
        self._buffer = np.empty((0, channels), dtype=np.int16)

    def add_chunk(
        self,
        audio_data: bytes,
        audio_format: AudioFormatLike,
    ) -> list[np.ndarray]:
        """Add one Sendspin chunk and return complete LC3-sized PCM frames."""
        pcm_format = audio_format.pcm_format
        samples = pcm_bytes_to_int16(
            audio_data,
            channels=pcm_format.channels,
            bit_depth=pcm_format.bit_depth,
            target_channels=self.channels,
        )
        if samples.size == 0:
            return []

        self._buffer = np.vstack([self._buffer, samples])
        frames = []
        while self._buffer.shape[0] >= self.samples_per_frame:
            frames.append(self._buffer[: self.samples_per_frame])
            self._buffer = self._buffer[self.samples_per_frame :]
        return frames


def pcm_buffer_capacity_bytes(
    *,
    sample_rate: int,
    channels: int,
    bit_depth: int,
    buffer_ms: int,
) -> int:
    """Return the PCM byte capacity for the advertised player buffer."""
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if channels <= 0:
        raise ValueError("channels must be positive")
    if bit_depth <= 0 or bit_depth % 8 != 0:
        raise ValueError("bit_depth must be a positive whole-byte depth")
    if buffer_ms <= 0:
        raise ValueError("buffer_ms must be positive")

    return max(
        1,
        sample_rate * channels * (bit_depth // 8) * buffer_ms // 1_000,
    )


def pcm_bytes_to_int16(
    audio_data: bytes,
    *,
    channels: int,
    bit_depth: int,
    target_channels: int,
) -> np.ndarray:
    """Convert little-endian PCM bytes to int16 samples."""
    if channels <= 0:
        raise ValueError("channels must be positive")
    if target_channels not in (1, 2):
        raise ValueError("target_channels must be 1 or 2")

    if bit_depth == 16:
        samples = np.frombuffer(audio_data, dtype="<i2").astype(np.int16, copy=False)
    elif bit_depth == 24:
        samples = _pcm24le_to_int16(audio_data)
    elif bit_depth == 32:
        samples = (np.frombuffer(audio_data, dtype="<i4") >> 16).astype(np.int16)
    else:
        raise ValueError(f"Unsupported PCM bit depth: {bit_depth}")

    complete_sample_count = samples.size - (samples.size % channels)
    samples = samples[:complete_sample_count]
    if samples.size == 0:
        return np.empty((0, target_channels), dtype=np.int16)

    shaped = samples.reshape(-1, channels)
    return convert_channels(shaped, target_channels)


def _pcm24le_to_int16(audio_data: bytes) -> np.ndarray:
    complete_bytes = len(audio_data) - (len(audio_data) % 3)
    if complete_bytes == 0:
        return np.empty(0, dtype=np.int16)

    raw = np.frombuffer(audio_data[:complete_bytes], dtype=np.uint8).reshape(-1, 3)
    signed = (
        raw[:, 0].astype(np.int32)
        | (raw[:, 1].astype(np.int32) << 8)
        | (raw[:, 2].astype(np.int32) << 16)
    )
    signed = (signed << 8) >> 8
    return (signed >> 8).astype(np.int16)


def convert_channels(samples: np.ndarray, target_channels: int) -> np.ndarray:
    """Convert PCM samples to mono or stereo."""
    current_channels = samples.shape[1]
    if current_channels == target_channels:
        return samples.astype(np.int16, copy=False)
    if current_channels == 1 and target_channels == 2:
        return np.repeat(samples, 2, axis=1).astype(np.int16, copy=False)
    if current_channels == 2 and target_channels == 1:
        return samples.mean(axis=1, keepdims=True).astype(np.int16)

    if current_channels > target_channels:
        return samples[:, :target_channels].astype(np.int16, copy=False)

    padding = np.zeros(
        (samples.shape[0], target_channels - current_channels), dtype=np.int16
    )
    return np.hstack([samples, padding]).astype(np.int16, copy=False)


async def run_sendspin_auracast(config: SendspinAuracastConfig) -> None:
    """Receive Sendspin PCM audio and broadcast it over Auracast."""
    from aiosendspin.client import SendspinClient
    from aiosendspin.models import AudioCodec, PlayerCommand, PlayerStateType, Roles
    from aiosendspin.models.player import (
        ClientHelloPlayerSupport,
        SupportedAudioFormat,
    )

    broadcaster = AuracastBroadcaster(config.transport_spec, config.broadcast)
    await broadcaster.start_async()

    frame_buffer = PcmFrameBuffer(
        channels=config.broadcast.channels,
        samples_per_frame=broadcaster.samples_per_frame,
    )
    player_audio_state = PlayerAudioState(volume=config.initial_volume)
    audio_queue: asyncio.Queue[tuple[int, bytes, AudioFormatLike] | None] = (
        asyncio.Queue(maxsize=config.queue_size)
    )
    disconnected = asyncio.Event()
    loop = asyncio.get_running_loop()

    player_support = ClientHelloPlayerSupport(
        supported_formats=[
            SupportedAudioFormat(
                codec=AudioCodec.PCM,
                channels=config.broadcast.channels,
                sample_rate=config.broadcast.sample_rate,
                bit_depth=16,
            )
        ],
        buffer_capacity=pcm_buffer_capacity_bytes(
            sample_rate=config.broadcast.sample_rate,
            channels=config.broadcast.channels,
            bit_depth=16,
            buffer_ms=config.sendspin_buffer_ms,
        ),
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
        item = (timestamp_us, audio_data, audio_format)

        def enqueue_item() -> None:
            try:
                audio_queue.put_nowait(item)
            except asyncio.QueueFull:
                try:
                    audio_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    audio_queue.put_nowait(item)
                    logger.warning(
                        "Dropped stale Sendspin audio chunk because queue is full"
                    )
                except asyncio.QueueFull:
                    logger.warning(
                        "Dropping Sendspin audio chunk because queue is full"
                    )

        loop.call_soon_threadsafe(enqueue_item)

    client.add_audio_chunk_listener(handle_audio_chunk)
    client.add_disconnect_listener(disconnected.set)

    async def publish_player_state() -> None:
        if not client.connected:
            return
        await client.send_player_state(
            state=PlayerStateType.SYNCHRONIZED,
            volume=player_audio_state.volume,
            muted=player_audio_state.muted,
        )

    def handle_server_command(payload: object) -> None:
        player_cmd = getattr(payload, "player", None)
        if player_cmd is None:
            return

        state_changed = False
        if (
            player_cmd.command == PlayerCommand.VOLUME
            and player_cmd.volume is not None
            and player_audio_state.volume != player_cmd.volume
        ):
            player_audio_state.volume = player_cmd.volume
            state_changed = True
            logger.info("Updated player volume to %d%%", player_audio_state.volume)
        elif (
            player_cmd.command == PlayerCommand.MUTE
            and player_cmd.mute is not None
            and player_audio_state.muted != player_cmd.mute
        ):
            player_audio_state.muted = player_cmd.mute
            state_changed = True
            logger.info("Updated player mute to %s", player_audio_state.muted)

        if state_changed:
            loop.create_task(publish_player_state())

    client.add_server_command_listener(handle_server_command)

    async def pump_audio() -> None:
        while True:
            item = await audio_queue.get()
            if item is None:
                return
            _timestamp_us, audio_data, audio_format = item
            for frame in frame_buffer.add_chunk(audio_data, audio_format):
                await broadcaster.send_audio_async(player_audio_state.apply(frame))

    pump_task = asyncio.create_task(pump_audio())
    try:
        logger.info("Connecting to Sendspin server at %s", config.url)
        await asyncio.wait_for(client.connect(config.url), config.connect_timeout)
        logger.info("Connected; broadcasting over %s", config.transport_spec)
        await disconnected.wait()
    finally:
        await client.disconnect()
        await audio_queue.put(None)
        await pump_task
        await broadcaster.stop_async()


async def broadcast_until_stopped(config: SendspinAuracastConfig) -> None:
    """Run the bridge until interrupted or disconnected."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass

    bridge_task = asyncio.create_task(run_sendspin_auracast(config))
    stop_task = asyncio.create_task(stop_event.wait())
    done, pending = await asyncio.wait(
        [bridge_task, stop_task], return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    for task in done:
        task.result()


def parse_manufacturer_data(entries: Sequence[str] | None) -> dict[int, bytes] | None:
    """Parse CLI manufacturer data entries."""
    if not entries:
        return None

    manufacturer_data: dict[int, bytes] = {}
    for entry in entries:
        company_id_str, hex_data = entry.split(":", 1)
        manufacturer_data[int(company_id_str, 0)] = bytes.fromhex(hex_data)
    return manufacturer_data
