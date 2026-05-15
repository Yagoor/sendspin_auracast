"""Auracast broadcaster using Bumble and LC3."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import lc3
import numpy as np
from bumble import device, hci, transport
from bumble.core import AdvertisingData
from bumble.profiles import bap, le_audio, pbp

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BroadcastConfig:
    """Configuration for an Auracast broadcast."""

    name: str = "Sendspin Auracast"
    broadcast_id: int = 0x123456
    sample_rate: int = 48_000
    frame_duration_us: int = 10_000
    bitrate: int = 80_000
    channels: int = 2
    presentation_delay_us: int = 40_000
    max_transport_latency_ms: int = 65
    broadcast_code: bytes | None = None
    manufacturer_data: dict[int, bytes] | None = None


class AuracastBroadcaster:
    """Broadcast PCM audio over Bluetooth LE Audio."""

    def __init__(
        self,
        transport_spec: str,
        config: BroadcastConfig | None = None,
    ) -> None:
        self.transport_spec = transport_spec
        self.config = config or BroadcastConfig()

        self._device: device.Device | None = None
        self._hci_transport: Any = None
        self._advertising_set: device.AdvertisingSet | None = None
        self._big: device.Big | None = None
        self._iso_streams: list[device.IsoPacketStream] = []
        self._encoder: lc3.Encoder | None = None
        self._running = False
        self._frame_size = 0
        self._samples_per_frame = 0
        self._packets_sent = 0

    def _setup_encoder(self) -> None:
        self._encoder = lc3.Encoder(
            frame_duration_us=self.config.frame_duration_us,
            sample_rate_hz=self.config.sample_rate,
            num_channels=self.config.channels,
        )
        self._frame_size = self._encoder.get_frame_bytes(self.config.bitrate)
        self._samples_per_frame = (
            self.config.sample_rate * self.config.frame_duration_us // 1_000_000
        )

    async def _setup_device(self) -> None:
        hci_transport = await transport.open_transport(self.transport_spec)
        self._hci_transport = hci_transport
        hci_source, hci_sink = hci_transport

        device_config = device.DeviceConfiguration(
            name=self.config.name,
            address=hci.Address("F0:F1:F2:F3:F4:F5"),
        )
        self._device = device.Device.from_config_with_hci(
            device_config, hci_source, hci_sink
        )
        await self._device.power_on()

        if not self._device.supports_le_periodic_advertising:
            raise RuntimeError(
                "Controller does not support LE Periodic Advertising "
                "(required for Auracast)"
            )

        broadcast_audio_announcement = bap.BroadcastAudioAnnouncement(
            broadcast_id=self.config.broadcast_id
        )
        metadata = le_audio.Metadata(entries=[])
        codec_config = bap.CodecSpecificConfiguration(
            sampling_frequency=self._get_sampling_frequency(),
            frame_duration=self._get_frame_duration(),
            octets_per_codec_frame=self._frame_size,
        )

        locations = [bap.AudioLocation.FRONT_LEFT, bap.AudioLocation.FRONT_RIGHT]
        bis_configs = [
            bap.BasicAudioAnnouncement.BIS(
                index=index + 1,
                codec_specific_configuration=bap.CodecSpecificConfiguration(
                    audio_channel_allocation=locations[index % len(locations)]
                ),
            )
            for index in range(self.config.channels)
        ]
        basic_audio_announcement = bap.BasicAudioAnnouncement(
            presentation_delay=self.config.presentation_delay_us,
            subgroups=[
                bap.BasicAudioAnnouncement.Subgroup(
                    codec_id=hci.CodingFormat(codec_id=hci.CodecID.LC3),
                    codec_specific_configuration=codec_config,
                    metadata=metadata,
                    bis=bis_configs,
                )
            ],
        )
        public_broadcast = pbp.PublicBroadcastAnnouncement(
            features=pbp.PublicBroadcastAnnouncement.Features(0),
            metadata=metadata,
        )

        name_bytes = self.config.name.encode()
        ad_structures = [
            (AdvertisingData.COMPLETE_LOCAL_NAME, name_bytes),
            (AdvertisingData.BROADCAST_NAME, name_bytes),
        ]
        if self.config.manufacturer_data:
            for company_id, vendor_data in self.config.manufacturer_data.items():
                company_id_bytes = company_id.to_bytes(2, byteorder="little")
                ad_structures.append(
                    (
                        AdvertisingData.MANUFACTURER_SPECIFIC_DATA,
                        company_id_bytes + vendor_data,
                    )
                )

        advertising_data = (
            bytes(AdvertisingData(ad_structures))
            + broadcast_audio_announcement.get_advertising_data()
            + public_broadcast.get_advertising_data()
        )

        self._advertising_set = await self._device.create_advertising_set(
            advertising_parameters=device.AdvertisingParameters(
                advertising_event_properties=device.AdvertisingEventProperties(
                    is_connectable=False
                ),
                primary_advertising_interval_min=60,
                primary_advertising_interval_max=60,
                advertising_sid=0,
            ),
            advertising_data=advertising_data,
            periodic_advertising_parameters=device.PeriodicAdvertisingParameters(
                periodic_advertising_interval_min=60,
                periodic_advertising_interval_max=60,
            ),
            periodic_advertising_data=basic_audio_announcement.get_advertising_data(),
            auto_restart=True,
            auto_start=True,
        )
        await self._advertising_set.start_periodic()

        self._big = await self._device.create_big(
            self._advertising_set,
            parameters=device.BigParameters(
                num_bis=self.config.channels,
                sdu_interval=self.config.frame_duration_us,
                max_sdu=self._frame_size,
                max_transport_latency=self.config.max_transport_latency_ms,
                rtn=4,
                broadcast_code=self.config.broadcast_code,
            ),
        )

        self._iso_streams = []
        for bis_link in self._big.bis_links:
            await bis_link.setup_data_path(
                direction=bis_link.Direction.HOST_TO_CONTROLLER
            )
            self._iso_streams.append(device.IsoPacketStream(bis_link, 64))

        logger.info(
            "Broadcasting %r with %d BIS streams",
            self.config.name,
            len(self._iso_streams),
        )

    def _get_sampling_frequency(self) -> bap.SamplingFrequency:
        freq_map = {
            8_000: bap.SamplingFrequency.FREQ_8000,
            16_000: bap.SamplingFrequency.FREQ_16000,
            24_000: bap.SamplingFrequency.FREQ_24000,
            32_000: bap.SamplingFrequency.FREQ_32000,
            44_100: bap.SamplingFrequency.FREQ_44100,
            48_000: bap.SamplingFrequency.FREQ_48000,
        }
        return freq_map.get(self.config.sample_rate, bap.SamplingFrequency.FREQ_48000)

    def _get_frame_duration(self) -> bap.FrameDuration:
        if self.config.frame_duration_us == 7_500:
            return bap.FrameDuration.DURATION_7500_US
        return bap.FrameDuration.DURATION_10000_US

    async def start_async(self) -> None:
        """Start advertising and create the broadcast ISO group."""
        if self._running:
            raise RuntimeError("Broadcaster is already running")

        self._running = True
        self._setup_encoder()
        await self._setup_device()

    async def send_audio_async(self, pcm_data: np.ndarray) -> None:
        """Encode and send PCM samples shaped as ``(samples, channels)``."""
        if not self._running or not self._encoder:
            return

        if pcm_data.ndim == 1:
            pcm_data = pcm_data.reshape(-1, 1)

        total_samples = pcm_data.shape[0]
        offset = 0
        while offset + self._samples_per_frame <= total_samples:
            frame_samples = pcm_data[offset : offset + self._samples_per_frame]
            offset += self._samples_per_frame

            interleaved = frame_samples.flatten().astype(np.int16)
            encoded = self._encoder.encode(
                interleaved.tobytes(),
                num_bytes=self._frame_size * self.config.channels,
                bit_depth=16,
            )

            write_tasks = []
            for index, stream in enumerate(self._iso_streams):
                start = index * self._frame_size
                end = start + self._frame_size
                write_tasks.append(stream.write(encoded[start:end]))
            await asyncio.gather(*write_tasks)

            self._packets_sent += self.config.channels
            if self._packets_sent % 200 == 0:
                logger.debug("ISO packets sent: %d", self._packets_sent)

    async def stop_async(self) -> None:
        """Stop the broadcast and release the Bluetooth controller."""
        self._running = False

        if self._big:
            try:
                await asyncio.wait_for(self._big.terminate(), timeout=2.0)
            except TimeoutError:
                logger.warning("BIG terminate timed out")
            self._big = None

        if self._advertising_set:
            try:
                await asyncio.wait_for(self._advertising_set.stop(), timeout=2.0)
            except TimeoutError:
                logger.warning("Advertising stop timed out")
            self._advertising_set = None

        if self._device:
            try:
                await asyncio.wait_for(self._device.power_off(), timeout=2.0)
            except TimeoutError:
                logger.warning("Device power off timed out")
            self._device = None

        self._hci_transport = None
        self._iso_streams = []

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def samples_per_frame(self) -> int:
        return self._samples_per_frame
