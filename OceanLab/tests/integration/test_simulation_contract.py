from __future__ import annotations

import unittest

from oceanlab.adapters.simulation import SimulatedSpectrometer
from oceanlab.domain.models import AcquisitionRequest


class SimulationContractTest(unittest.TestCase):
    def test_connect_configure_acquire_disconnect(self) -> None:
        adapter = SimulatedSpectrometer()
        connected = adapter.connect()
        request = AcquisitionRequest(20_000, 2, 1, 50)
        context = adapter.apply_configuration(request)
        frame = adapter.acquire(context)
        self.assertEqual(len(frame.values), connected.info.pixels)
        self.assertEqual(frame.context.integration_us, 20_000)
        self.assertTrue(all(0.0 <= value <= connected.info.max_intensity for value in frame.values))
        adapter.disconnect()
        self.assertIsNone(adapter.connected)


if __name__ == "__main__":
    unittest.main()
