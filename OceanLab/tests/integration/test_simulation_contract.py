from __future__ import annotations

import unittest

from oceanlab.adapters.simulation import SimulatedSpectrometer
from oceanlab.domain.models import AcquisitionRequest


class SimulationContractTest(unittest.TestCase):
    def test_connect_configure_acquire_disconnect(self) -> None:
        adapter = SimulatedSpectrometer()
        connected = adapter.connect()
        self.assertEqual(connected.info.max_intensity, 63_000.0)
        self.assertEqual(connected.info.saturation_intensity, 63_000.0)
        request = AcquisitionRequest(20_000, 2, 1, 50)
        context = adapter.apply_configuration(request)
        frame = adapter.acquire(context)
        self.assertEqual(len(frame.values), connected.info.pixels)
        self.assertEqual(frame.context.integration_us, 20_000)
        self.assertTrue(all(0.0 <= value <= connected.info.max_intensity for value in frame.values))
        saturated_context = adapter.apply_configuration(AcquisitionRequest(1_000_000, 1, 0, 50))
        saturated_frame = adapter.acquire(saturated_context)
        self.assertEqual(max(saturated_frame.values), 63_000.0)
        adapter.disconnect()
        self.assertIsNone(adapter.connected)


if __name__ == "__main__":
    unittest.main()
