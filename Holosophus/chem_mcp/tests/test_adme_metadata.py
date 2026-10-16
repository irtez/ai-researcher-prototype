import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from adme_metadata import (
    get_property_metadata,
    prediction_warnings,
    fingerprint_weights,
)


class ADMEMetadataTests(unittest.TestCase):
    def test_metadata_comes_from_installed_catalog_without_benchmark_accuracy(self):
        fields = [
            "HIA_Hou",
            "Solubility_AqSolDB",
            "Pgp_Broccatelli",
            "VDss_Lombardo",
            "Caco2_Wang",
            "missing",
        ]
        info = get_property_metadata(fields)
        self.assertEqual(info["HIA_Hou"]["range"], {"minimum": 0.0, "maximum": 1.0})
        self.assertEqual(info["HIA_Hou"]["units"], None)
        self.assertEqual(info["VDss_Lombardo"]["units"], "L/kg")
        self.assertEqual(
            info["Solubility_AqSolDB"]["range"], {"minimum": None, "maximum": None}
        )
        self.assertFalse(info["missing"]["available"])
        self.assertIn("units_warning", info["Caco2_Wang"])
        self.assertNotIn("AUROC", json.dumps(info))
        json.dumps(info, allow_nan=False)

    def test_missing_catalog_is_explicit_and_does_not_invent_meanings(self):
        with patch(
            "admet_ai.admet_info.get_admet_info", side_effect=OSError("missing")
        ):
            info = get_property_metadata(["HIA_Hou"])
        self.assertFalse(info["HIA_Hou"]["available"])
        self.assertNotIn("name", info["HIA_Hou"])

    def test_catalog_changes_are_used_rather_than_hardcoded_names(self):
        rows = pd.DataFrame(
            [
                {
                    "id": "custom",
                    "name": "Custom endpoint",
                    "task_type": "regression",
                    "units": "mg/L",
                    "minimum": 0,
                    "maximum": 20,
                    "url": "https://example.test/source",
                }
            ]
        )
        with patch("admet_ai.admet_info.get_admet_info", return_value=rows):
            info = get_property_metadata(["custom"])
        self.assertEqual(info["custom"]["name"], "Custom endpoint")
        self.assertEqual(info["custom"]["units"], "mg/L")

    def test_invalid_values_are_flagged_without_clamping_or_unit_conversion(self):
        values = {
            "HIA_Hou": 1.2,
            "VDss_Lombardo": -2.15,
            "Solubility_AqSolDB": -4.2,
            "Caco2_Wang": -4.7,
        }
        info = get_property_metadata(list(values))
        warnings = prediction_warnings(values, info)
        self.assertEqual(
            {w["property"] for w in warnings if w["code"] == "outside_declared_range"},
            {"HIA_Hou", "VDss_Lombardo"},
        )
        self.assertEqual(values["VDss_Lombardo"], -2.15)
        self.assertEqual(values["Caco2_Wang"], -4.7)
        warnings = prediction_warnings({"HIA_Hou": None}, info)
        self.assertEqual(warnings[0]["code"], "missing_or_non_numeric")

    def test_weight_identity_changes_with_contents_not_directory_location(self):
        with tempfile.TemporaryDirectory() as root:
            a, b = Path(root) / "a", Path(root) / "b"
            a.mkdir()
            b.mkdir()
            for folder in (a, b):
                (folder / "model.pt").write_bytes(b"first")
            self.assertEqual(fingerprint_weights(a), fingerprint_weights(b))
            (b / "model.pt").write_bytes(b"second")
            self.assertNotEqual(fingerprint_weights(a), fingerprint_weights(b))


if __name__ == "__main__":
    unittest.main()
