from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from oceanlab import cli
from oceanlab.domain.models import AcquisitionRequest, DeviceCapabilities


class CommandLineDefaultsTest(unittest.TestCase):
    def test_once_uses_same_initial_parameters_as_gui_and_respects_capabilities(self):
        for supported in (True, False):
            with self.subTest(supported=supported):
                capabilities = DeviceCapabilities(supported, supported, supported, supported)
                info = SimpleNamespace(device_id=7, model="QE65000", serial="TEST", pixels=2,
                                       wavelength_min=400.0, wavelength_max=401.0,
                                       capabilities=capabilities)
                old = AcquisitionRequest(250_000, 20, 5, 90, electric_dark=False,
                                         nonlinearity=True, trigger_mode=1, light_source=True)
                connected = SimpleNamespace(info=info, settings=old, wavelengths=(400.0, 401.0))
                adapter = mock.Mock()
                adapter.initialize.return_value = (3, 1, 4)
                adapter.connect.return_value = connected
                adapter.apply_configuration.side_effect = lambda request: request
                adapter.acquire.return_value = SimpleNamespace(values=(1.0, 2.0))
                with mock.patch.object(cli, "IsolatedSpectrometer", return_value=adapter), \
                        mock.patch.object(cli, "diagnostic_output"):
                    self.assertEqual(cli.probe_device(Path("unused"), acquire_once=True), 0)
                request = adapter.apply_configuration.call_args[0][0]
                self.assertEqual((request.integration_us, request.averages, request.boxcar), (100_000, 1, 0))
                self.assertEqual((request.trigger_mode, request.nonlinearity, request.light_source), (0, False, False))
                self.assertEqual(request.electric_dark, supported)
                self.assertEqual(request.apply_electric_dark, supported)
                self.assertEqual(request.apply_nonlinearity, supported)
                self.assertEqual(request.apply_trigger, supported)
                self.assertEqual(request.apply_light_source, supported)
                adapter.acquire.assert_called_once_with(request)
                adapter.shutdown.assert_called_once_with()
