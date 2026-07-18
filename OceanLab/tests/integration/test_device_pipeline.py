from __future__ import annotations
import queue
import unittest
from pathlib import Path

from oceanlab.adapters.oceandirect import OceanDirectSpectrometer
from oceanlab.application.acquisition import AcquisitionService, CoalescingEventQueue
from oceanlab.config import TRIGGER_MODE_TO_VALUE, TRIGGER_VALUE_TO_LABEL
from oceanlab.domain.models import (
    AcquisitionRequest,
    ConnectedDevice,
    DeviceCapabilities,
    DeviceInfo,
)


class FakeAdvanced:
    def __init__(self) -> None:
        self.lamp = False

    def set_enable_lamp(self, value: bool) -> None:
        self.lamp = bool(value)

    def get_enable_lamp(self) -> bool:
        return self.lamp


class FakeDevice:
    status = "open"

    def __init__(self, reject_integration: int | None = None) -> None:
        self.integration = 100
        self.averages = 1
        self.boxcar = 0
        self.electric_dark = False
        self.nonlinearity = False
        self.trigger_mode = 0
        self.reject_integration = reject_integration
        self.Advanced = FakeAdvanced()

    def set_integration_time(self, value: int) -> None:
        if value != self.reject_integration:
            self.integration = int(value)

    def get_integration_time(self) -> int:
        return self.integration

    def set_scans_to_average(self, value: int) -> None:
        self.averages = int(value)

    def get_scans_to_average(self) -> int:
        return self.averages

    def set_boxcar_width(self, value: int) -> None:
        self.boxcar = int(value)

    def get_boxcar_width(self) -> int:
        return self.boxcar

    def set_electric_dark_correction_usage(self, value: bool) -> None:
        self.electric_dark = bool(value)

    def get_electric_dark_correction_usage(self) -> bool:
        return self.electric_dark

    def set_nonlinearity_correction_usage(self, value: bool) -> None:
        self.nonlinearity = bool(value)

    def get_nonlinearity_correction_usage(self) -> bool:
        return self.nonlinearity

    def set_trigger_mode(self, value: int) -> None:
        self.trigger_mode = int(value)

    def get_trigger_mode(self) -> int:
        return self.trigger_mode

    def get_formatted_spectrum(self) -> list[float]:
        return [10.0, 20.0, 30.0]


def make_adapter(device: FakeDevice) -> OceanDirectSpectrometer:
    adapter = OceanDirectSpectrometer(Path("missing-sdk"))
    info = DeviceInfo(
        device_id=7,
        serial="TEST123",
        model="FAKE",
        pixels=3,
        wavelength_min=350.0,
        wavelength_max=352.0,
        integration_min_us=1,
        integration_max_us=1_000_000,
        integration_increment_us=1,
        max_intensity=65_535.0,
        capabilities=DeviceCapabilities(True, True, True, True),
    )
    adapter.device = device
    adapter.device_id = 7
    adapter.connected = ConnectedDevice(
        info,
        (350.0, 351.0, 352.0),
        AcquisitionRequest(100, 1, 0, 80),
    )
    return adapter


class DevicePipelineTest(unittest.TestCase):
    def test_unconfigured_start_is_rejected(self) -> None:
        events = CoalescingEventQueue()
        service = AcquisitionService(lambda: make_adapter(FakeDevice()), events)
        with self.assertRaisesRegex(RuntimeError, "尚未由仪器确认"):
            service._start()
        self.assertFalse(service.running)
        self.assertTrue(events.empty())

    def test_spectrum_events_are_coalesced_without_reordering_control_events(self) -> None:
        events = CoalescingEventQueue()
        events.put(("spectrum", {"frame": 1}))
        events.put(("spectrum", {"frame": 2}))
        events.put(("status", {"message": "boundary"}))
        events.put(("spectrum", {"frame": 3}))
        events.put(("spectrum", {"frame": 4}))
        self.assertEqual(events.qsize(), 3)
        self.assertEqual(events.get_nowait()[1]["frame"], 2)
        self.assertEqual(events.get_nowait()[0], "status")
        self.assertEqual(events.get_nowait()[1]["frame"], 4)
        with self.assertRaises(queue.Empty):
            events.get_nowait()

    def test_rejected_readback_never_creates_context(self) -> None:
        adapter = make_adapter(FakeDevice(reject_integration=200))
        with self.assertRaisesRegex(RuntimeError, "读回值"):
            adapter.apply_configuration(AcquisitionRequest(200, 2, 1, 50))
        self.assertEqual(adapter.device.integration, 100)

    def test_successful_configuration_attaches_actual_context(self) -> None:
        adapter = make_adapter(FakeDevice())
        request = AcquisitionRequest(
            integration_us=250,
            averages=3,
            boxcar=2,
            interval_ms=60,
            electric_dark=True,
            nonlinearity=True,
            trigger_mode=1,
            light_source=True,
            apply_electric_dark=True,
            apply_nonlinearity=True,
            apply_trigger=True,
            apply_light_source=True,
        )
        context = adapter.apply_configuration(request)
        frame = adapter.acquire(context)
        self.assertEqual(frame.context.integration_us, 250)
        self.assertEqual(frame.context.trigger_mode, 1)
        self.assertTrue(frame.context.electric_dark)
        self.assertTrue(frame.context.light_source)
        self.assertEqual(frame.values, (10.0, 20.0, 30.0))

    def test_closed_sdk_cache_entry_is_removed(self) -> None:
        class FakeAPI:
            def __init__(self) -> None:
                self.open_devices = {7: object(), 8: object()}

            def close_device(self, _device_id: int) -> None:
                pass

        adapter = make_adapter(FakeDevice())
        adapter.api = FakeAPI()
        adapter.disconnect()
        self.assertNotIn(7, adapter.api.open_devices)
        self.assertIn(8, adapter.api.open_devices)

    def test_trigger_labels_match_oceandirect_values(self) -> None:
        self.assertEqual(TRIGGER_MODE_TO_VALUE["按需 / 正常"], 0)
        self.assertEqual(TRIGGER_MODE_TO_VALUE["软件触发"], 1)
        self.assertEqual(TRIGGER_VALUE_TO_LABEL[1], "软件触发")


if __name__ == "__main__":
    unittest.main()
