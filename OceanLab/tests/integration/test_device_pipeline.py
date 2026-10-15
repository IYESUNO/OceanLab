from __future__ import annotations
import queue
import threading
import unittest
from unittest import mock
from pathlib import Path

from oceanlab.adapters.oceandirect import OceanDirectSpectrometer, default_saturation_intensity
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

    def nonlinearity_correct_spectrum2(self, dark, values):
        return list(values)

    def boxcar_correct_spectrum(self, values, width):
        return list(values)


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
    def test_detector_maximum_is_not_replaced_with_a_corrected_plateau(self) -> None:
        self.assertEqual(default_saturation_intensity("QE65000", 65_535.0), 65_535.0)
        self.assertEqual(default_saturation_intensity("QE65 Pro", 65_535.0), 65_535.0)
        self.assertEqual(default_saturation_intensity("FAKE", 65_535.0), 65_535.0)

    def test_official_sdk_is_the_only_acquisition_processing_path(self) -> None:
        device = FakeDevice()
        returned = [-8032.8125, 55037.2875, -6990.9625]
        device.get_formatted_spectrum = mock.Mock(return_value=returned)
        # These vendor methods perform a separate correction route. Calling
        # them on the configured spectrum would duplicate/conflict correction.
        device.get_dark_corrected_spectrum1 = mock.Mock()
        device.get_nonlinearity_corrected_spectrum1 = mock.Mock()
        device.boxcar_correct_spectrum = mock.Mock()
        adapter = make_adapter(device)
        request = AcquisitionRequest(100_000, 1, 3, 80, electric_dark=True,
                                     nonlinearity=False, apply_electric_dark=True,
                                     apply_nonlinearity=True)
        context = adapter.apply_configuration(request)
        frame = adapter.acquire(context)
        self.assertEqual((device.averages, device.boxcar), (1, 3))
        self.assertTrue(device.electric_dark)
        self.assertFalse(device.nonlinearity)
        self.assertEqual(frame.values, tuple(returned))
        device.get_formatted_spectrum.assert_called_once_with()
        device.get_dark_corrected_spectrum1.assert_not_called()
        device.get_nonlinearity_corrected_spectrum1.assert_not_called()
        device.boxcar_correct_spectrum.assert_not_called()

    def test_batch_average_precedes_official_nonlinearity_and_boxcar(self) -> None:
        device = FakeDevice()
        adapter = make_adapter(device)
        context = adapter.apply_configuration(AcquisitionRequest(
            100_000, 2, 3, 80, electric_dark=True, nonlinearity=True,
            apply_electric_dark=True, apply_nonlinearity=True,
        ))
        calls = []
        scans = iter(([2.0, 4.0, 6.0], [4.0, 8.0, 12.0]))
        def read():
            self.assertEqual((device.averages, device.boxcar, device.nonlinearity), (1, 0, False))
            self.assertTrue(device.electric_dark)
            calls.append("read")
            return next(scans)
        def nlc(dark, values):
            self.assertFalse(device.electric_dark)
            self.assertFalse(device.nonlinearity)
            self.assertEqual(dark, [0.0, 0.0, 0.0])
            self.assertEqual(values, [3.0, 6.0, 9.0])
            calls.append("nlc")
            return [value ** 2 for value in values]
        def boxcar(values, width):
            self.assertEqual((values, width), ([9.0, 36.0, 81.0], 3))
            calls.append("boxcar")
            return values
        device.get_formatted_spectrum = read
        device.nonlinearity_correct_spectrum2 = nlc
        device.boxcar_correct_spectrum = boxcar
        frame = adapter.acquire(context)
        self.assertEqual(frame.values, (9.0, 36.0, 81.0))
        self.assertEqual(frame.context.averages, 2)
        self.assertEqual(calls, ["read", "read", "nlc", "boxcar"])
        self.assertEqual((device.averages, device.boxcar, device.nonlinearity), (2, 3, True))
        self.assertTrue(device.electric_dark)

    def test_pause_after_current_scan_skips_remaining_average_and_restores_settings(self) -> None:
        device = FakeDevice()
        adapter = make_adapter(device)
        pause = threading.Event()
        adapter.bind_pause_event(pause)
        context = adapter.apply_configuration(AcquisitionRequest(3_000_000, 20, 0, 80))
        def read():
            self.assertEqual(device.averages, 1)
            pause.set()
            return [10.0, 20.0, 30.0]
        device.get_formatted_spectrum = mock.Mock(side_effect=read)
        self.assertIsNone(adapter.acquire(context))
        device.get_formatted_spectrum.assert_called_once_with()
        self.assertEqual(device.averages, 20)
        pause.clear()
        device.get_formatted_spectrum = mock.Mock(return_value=[10.0, 20.0, 30.0])
        self.assertEqual(adapter.acquire(context).values, (10.0, 20.0, 30.0))
        self.assertEqual(device.get_formatted_spectrum.call_count, 20)

    def test_scan_failure_restores_batch_configuration_and_rejects_data(self) -> None:
        device = FakeDevice()
        adapter = make_adapter(device)
        context = adapter.apply_configuration(AcquisitionRequest(100, 20, 3, 80))
        device.get_formatted_spectrum = mock.Mock(side_effect=[[1.0, 2.0, 3.0], [float("nan"), 2.0, 3.0]])
        with self.assertRaisesRegex(RuntimeError, "非有限"):
            adapter.acquire(context)
        self.assertEqual((device.averages, device.boxcar), (20, 3))

    def test_pause_restoration_failure_is_not_a_successful_pause(self) -> None:
        device = FakeDevice()
        adapter = make_adapter(device)
        pause = threading.Event()
        adapter.bind_pause_event(pause)
        context = adapter.apply_configuration(AcquisitionRequest(100, 20, 0, 80))
        def read():
            pause.set()
            return [1.0, 2.0, 3.0]
        device.get_formatted_spectrum = read
        device.set_scans_to_average = lambda value: setattr(device, "averages", 1)
        with self.assertRaisesRegex(RuntimeError, "读回不一致"):
            adapter.acquire(context)

    def test_coupled_sdk_switch_is_rejected_after_all_writes(self) -> None:
        class CoupledDevice(FakeDevice):
            def set_nonlinearity_correction_usage(self, value):
                super().set_nonlinearity_correction_usage(value)
                if value:
                    self.electric_dark = True
        adapter = make_adapter(CoupledDevice())
        request = AcquisitionRequest(100, 1, 0, 80, electric_dark=False,
                                     nonlinearity=True, apply_electric_dark=True,
                                     apply_nonlinearity=True)
        with self.assertRaisesRegex(RuntimeError, "electric_dark 最终读回"):
            adapter.apply_configuration(request)
        self.assertFalse(adapter.device.electric_dark)
        self.assertFalse(adapter.device.nonlinearity)

    def test_unrequested_coupled_switch_records_final_actual_value(self) -> None:
        class CoupledDevice(FakeDevice):
            def set_nonlinearity_correction_usage(self, value):
                super().set_nonlinearity_correction_usage(value)
                if value:
                    self.electric_dark = True
        adapter = make_adapter(CoupledDevice())
        context = adapter.apply_configuration(AcquisitionRequest(
            100, 1, 0, 80, nonlinearity=True, apply_nonlinearity=True,
        ))
        self.assertTrue(context.electric_dark)
        self.assertTrue(context.nonlinearity)

    def test_optional_setter_cannot_silently_change_core_settings(self) -> None:
        class CoupledDevice(FakeDevice):
            def set_trigger_mode(self, value):
                super().set_trigger_mode(value)
                self.boxcar = 9
        adapter = make_adapter(CoupledDevice())
        with self.assertRaisesRegex(RuntimeError, "boxcar 最终读回"):
            adapter.apply_configuration(AcquisitionRequest(100, 1, 0, 80,
                                                           trigger_mode=1, apply_trigger=True))

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

    def test_unmodified_optional_settings_are_preserved_in_context(self) -> None:
        device = FakeDevice()
        device.electric_dark = True
        device.nonlinearity = True
        device.trigger_mode = 1
        device.Advanced.lamp = True
        adapter = make_adapter(device)
        context = adapter.apply_configuration(AcquisitionRequest(250, 2, 1, 80))
        self.assertTrue(context.electric_dark)
        self.assertTrue(context.nonlinearity)
        self.assertEqual(context.trigger_mode, 1)
        self.assertTrue(context.light_source)

    def test_unchanged_settings_are_verified_without_rewriting_device(self) -> None:
        device = FakeDevice()
        setter_names = ("set_integration_time", "set_scans_to_average", "set_boxcar_width",
                        "set_electric_dark_correction_usage", "set_nonlinearity_correction_usage", "set_trigger_mode")
        for name in setter_names:
            setattr(device, name, mock.Mock(wraps=getattr(device, name)))
        device.Advanced.set_enable_lamp = mock.Mock(wraps=device.Advanced.set_enable_lamp)
        device.get_integration_time = mock.Mock(wraps=device.get_integration_time)
        adapter = make_adapter(device)
        request = AcquisitionRequest(100, 1, 0, 80, apply_electric_dark=True,
                                     apply_nonlinearity=True, apply_trigger=True, apply_light_source=True)
        context = adapter.apply_configuration(request)
        self.assertEqual(context.integration_us, 100)
        self.assertGreaterEqual(device.get_integration_time.call_count, 2)
        for name in setter_names:
            getattr(device, name).assert_not_called()
        device.Advanced.set_enable_lamp.assert_not_called()

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

    def test_failed_close_still_clears_adapter_state_and_cache(self) -> None:
        class FailingAPI:
            def __init__(self) -> None:
                self.open_devices = {7: object()}

            def close_device(self, _device_id: int) -> None:
                raise RuntimeError("USB device already gone")

        adapter = make_adapter(FakeDevice())
        adapter.api = FailingAPI()
        with self.assertRaisesRegex(RuntimeError, "already gone"):
            adapter.disconnect()
        self.assertIsNone(adapter.device)
        self.assertIsNone(adapter.device_id)
        self.assertIsNone(adapter.connected)
        self.assertNotIn(7, adapter.api.open_devices)

    def test_idle_connection_check_emits_disconnect_and_error(self) -> None:
        class DisconnectedSpectrometer:
            def disconnect(self) -> None:
                raise RuntimeError("device vanished")

            def check_connection(self) -> bool:
                return False

        events = CoalescingEventQueue()
        service = AcquisitionService(lambda: DisconnectedSpectrometer(), events)
        service.spectrometer = DisconnectedSpectrometer()
        service.connected = make_adapter(FakeDevice()).connected
        service._check_connection()
        self.assertIsNone(service.connected)
        kinds = []
        while not events.empty():
            kinds.append(events.get_nowait().kind)
        self.assertIn("disconnected", kinds)
        self.assertIn("error", kinds)

    def test_trigger_labels_match_oceandirect_values(self) -> None:
        self.assertEqual(TRIGGER_MODE_TO_VALUE["按需 / 正常"], 0)
        self.assertEqual(TRIGGER_MODE_TO_VALUE["软件触发"], 1)
        self.assertEqual(TRIGGER_VALUE_TO_LABEL[1], "软件触发")


if __name__ == "__main__":
    unittest.main()
