import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from adme_prediction import ADMEPrediction, ADMESQLiteCache, profile_molecule_physchem_and_adme_nn
from adme_tools import TOOL_REGISTRY, tool_profile_molecule_physchem_and_adme_nn


class _FakeADMETModel:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def predict(self, smiles: list[str]) -> pd.DataFrame:
        self.calls.append(list(smiles))
        rows = []
        for value in smiles:
            rows.append(
                {
                    "Solubility_AqSolDB": -0.2,
                    "HIA_Hou": 0.85,
                    "Caco2_Wang": 1.25 if value == "CCO" else 0.75,
                    "BBB_Martins": 0.1,
                    "Pgp_Broccatelli": 0.3,
                    "PPBR_AZ": 70.0,
                    "VDss_Lombardo": 0.6,
                    "Clearance_Hepatocyte_AZ": 5.0,
                    "Clearance_Microsome_AZ": 6.0,
                    "Bioavailability_Ma": 0.9 if value == "CCO" else 0.4,
                    "CYP1A2_Veith": 0.1,
                    "CYP2C9_Veith": 0.2,
                    "CYP2C19_Veith": 0.3,
                    "CYP2D6_Veith": 0.4,
                    "CYP3A4_Veith": 0.5,
                    "AMES": 0.99,
                }
            )
        return pd.DataFrame(rows, index=smiles)


class ADMEPredictionTests(unittest.TestCase):
    def test_predict_adme_batches_uncached_molecules_and_uses_sqlite_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "admet_ai.sqlite"
            fake_model = _FakeADMETModel()

            with patch("adme_prediction._get_model", return_value=fake_model):
                first_predictions = profile_molecule_physchem_and_adme_nn(
                    ["CCO", "CC(=O)O"],
                    cache_path=cache_path,
                )

            self.assertIsInstance(first_predictions, list)
            assert isinstance(first_predictions, list)
            self.assertEqual(fake_model.calls, [["CCO", "CC(=O)O"]])
            self.assertEqual(first_predictions[0].connectivity_smiles, "CCO")
            self.assertEqual(first_predictions[0].molecular_formula, "C2H6O")
            self.assertAlmostEqual(first_predictions[0].molecular_weight or 0.0, 46.069, places=3)
            self.assertIn("logp", first_predictions[0].descriptors)
            self.assertEqual(first_predictions[0].adme_predictions["Caco2_Wang"], 1.25)
            self.assertEqual(first_predictions[0].adme_predictions["Solubility_AqSolDB"], -0.2)
            self.assertEqual(first_predictions[1].adme_predictions["Bioavailability_Ma"], 0.4)
            self.assertNotIn("AMES", first_predictions[0].adme_predictions)
            self.assertFalse(first_predictions[0].to_dict()["cached"])

            with sqlite3.connect(cache_path) as connection:
                cached_count = connection.execute("SELECT COUNT(*) FROM adme_prediction_cache").fetchone()[0]
                cached_json = connection.execute("SELECT response_json FROM adme_prediction_cache LIMIT 1").fetchone()[0]
            self.assertEqual(cached_count, 2)
            self.assertNotIn("descriptors", cached_json)
            self.assertNotIn("molecular_formula", cached_json)

            with patch("adme_prediction._get_model", side_effect=AssertionError("model should not load")):
                cached_predictions = profile_molecule_physchem_and_adme_nn(
                    ["OCC", "CC(=O)O"],
                    cache_path=cache_path,
                )

            self.assertIsInstance(cached_predictions, list)
            assert isinstance(cached_predictions, list)
            self.assertEqual(cached_predictions[0].input_smiles, "OCC")
            self.assertEqual(cached_predictions[0].connectivity_smiles, "CCO")
            self.assertEqual(cached_predictions[0].molecular_formula, "C2H6O")
            self.assertIn("logp", cached_predictions[0].descriptors)
            self.assertEqual(cached_predictions[0].adme_predictions["Caco2_Wang"], 1.25)
            self.assertTrue(all(item.cached for item in cached_predictions))

            with patch("adme_prediction._get_model", return_value=fake_model):
                mixed = profile_molecule_physchem_and_adme_nn(["OCC", "CCC", "CCC"], cache_path=cache_path)
            self.assertEqual([item.cached for item in mixed], [True, False, False])
            self.assertEqual(fake_model.calls[-1], ["CCC"])

    def test_legacy_and_other_model_caches_are_not_relabelled_as_current(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.sqlite"
            ADMESQLiteCache(cache_path).set(molecule_key="smiles:CCO", connectivity_smiles="CCO",
                                          adme_predictions={"HIA_Hou": 0.123})
            fake_model = _FakeADMETModel()
            with patch("adme_prediction._get_model", return_value=fake_model):
                first = profile_molecule_physchem_and_adme_nn("CCO", cache_path=cache_path)
                with patch("adme_prediction.get_model_info", return_value={"id": "a-different-model"}):
                    second = profile_molecule_physchem_and_adme_nn("CCO", cache_path=cache_path)
            self.assertEqual(fake_model.calls, [["CCO"], ["CCO"]])
            self.assertFalse(first.cached)
            self.assertFalse(second.cached)
            self.assertEqual(first.adme_predictions["HIA_Hou"], 0.85)
            self.assertEqual(ADMESQLiteCache(cache_path).get(molecule_key="smiles:CCO"), {"HIA_Hou": 0.123})

    def test_predict_adme_raises_for_invalid_smiles_without_loading_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "admet_ai.sqlite"

            with patch("adme_prediction._get_model", side_effect=AssertionError("model should not load")):
                with self.assertRaisesRegex(ValueError, r"C1CC is invalid SMILES structure\."):
                    profile_molecule_physchem_and_adme_nn("C1CC", cache_path=cache_path)

            self.assertFalse(cache_path.exists())

    def test_stereoisomers_have_distinct_cache_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_model = _FakeADMETModel()
            with patch("adme_prediction._get_model", return_value=fake_model):
                predictions = profile_molecule_physchem_and_adme_nn(
                    ["C[C@H](O)C(=O)O", "C[C@@H](O)C(=O)O"], cache_path=Path(tmpdir) / "cache.sqlite"
                )
            self.assertEqual(len(fake_model.calls[0]), 2)
            self.assertNotEqual(predictions[0].connectivity_smiles, predictions[1].connectivity_smiles)

    def test_tool_predict_adme_exposes_batch_payload_and_error_payload(self) -> None:
        prediction = ADMEPrediction(
            input_smiles="CCO",
            connectivity_smiles="CCO",
            is_valid=True,
            molecular_formula="C2H6O",
            molecular_weight=46.069,
            exact_mass=46.041865,
            descriptors={"logp": -0.0014},
            adme_predictions={"Caco2_Wang": 1.25, "VDss_Lombardo": -2.15},
            cached=True,
        )

        with patch("adme_tools.profile_molecule_physchem_and_adme_nn", return_value=[prediction]):
            payload = tool_profile_molecule_physchem_and_adme_nn(["CCO"])

        self.assertTrue(payload["ok"])
        self.assertEqual(set(payload), {"ok", "results", "model", "property_metadata", "interpretation"})
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["molecular_formula"], "C2H6O")
        self.assertEqual(payload["results"][0]["adme_predictions"]["Caco2_Wang"], 1.25)
        self.assertIn("profile_molecule_physchem_and_adme_nn", TOOL_REGISTRY)
        self.assertEqual(payload["property_metadata"]["HIA_Hou"]["task_type"], "classification")
        self.assertIn("Inhibition", payload["property_metadata"]["Pgp_Broccatelli"]["name"])
        self.assertEqual(payload["property_metadata"]["Solubility_AqSolDB"]["units"], "log(mol/L)")
        self.assertTrue(payload["model"]["package_version"])
        self.assertEqual(len(payload["model"]["weights_sha256"]), 64)
        self.assertIn("cached", payload["results"][0])
        self.assertTrue(payload["results"][0]["cached"])
        self.assertEqual(payload["results"][0]["adme_predictions"]["VDss_Lombardo"], -2.15)
        self.assertEqual(payload["results"][0]["prediction_warnings"][0]["code"], "outside_declared_range")
        json.dumps(payload, allow_nan=False)

        error_payload = tool_profile_molecule_physchem_and_adme_nn([])
        self.assertFalse(error_payload["ok"])
        self.assertEqual(set(error_payload), {"ok", "error_type", "error"})
        self.assertEqual(error_payload["error_type"], "ValueError")


if __name__ == "__main__":
    unittest.main()
