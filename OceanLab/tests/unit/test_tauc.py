from __future__ import annotations

import math
import unittest

from oceanlab.domain.result_processing import calculate_tauc_values, photon_energy_ev


class TaucTest(unittest.TestCase):
    def test_direct_and_indirect_domains_preserve_zero_and_invalid_points(self):
        wavelengths = (400, 410, 420, 430)
        absorbance = (0.3, 0.0, -0.3, float('nan'))
        direct, direct_invalid = calculate_tauc_values(wavelengths, absorbance, 2)
        indirect, indirect_invalid = calculate_tauc_values(wavelengths, absorbance, 0.5)
        self.assertEqual((direct_invalid, indirect_invalid), (1, 2))
        self.assertAlmostEqual(direct[0], (0.3 * photon_energy_ev(400)) ** 2)
        self.assertAlmostEqual(indirect[0], math.sqrt(0.3 * photon_energy_ev(400)))
        self.assertEqual((direct[1], indirect[1]), (0.0, 0.0))
        self.assertGreater(direct[2], 0)
        self.assertTrue(math.isnan(indirect[2]))
        self.assertTrue(math.isnan(indirect[3]))

    def test_nonfinite_wavelength_and_overflow_are_invalid(self):
        for exponent in (2, 0.5):
            values, invalid = calculate_tauc_values((0, -1, float('inf'), 400), (1, 1, 1, 1e308), exponent)
            self.assertEqual(invalid, 4)
            self.assertTrue(all(math.isnan(value) for value in values))

    def test_unsupported_exponent_and_unpaired_data_are_rejected(self):
        with self.assertRaises(ValueError):
            calculate_tauc_values((400,), (1,), 1)
        with self.assertRaises(ValueError):
            calculate_tauc_values((400, 410), (1,), 0.5)
