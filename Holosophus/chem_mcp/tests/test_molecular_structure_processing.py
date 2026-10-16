import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from molecular_structure_processing import (
    ConnectedMoleculeDataset,
    MolecularStructureError,
    SmilesDatasetSource,
    analyze_smiles,
    connect_smiles_datasets,
)
from molecular_structure_tools import (
    tool_build_structure_research_report,
    tool_profile_molecule_physchem_and_adme_heuristic,
    tool_search_connected_datasets,
)


class MolecularStructureProcessingTests(unittest.TestCase):
    def test_heuristic_tool_explains_each_output_without_changing_legacy_results(self):
        payload = tool_profile_molecule_physchem_and_adme_heuristic(["CCO", "CC(=O)O", "CC(=O)C"])
        self.assertTrue(payload["ok"])
        self.assertIn("property_metadata", payload)
        metadata = payload["property_metadata"]
        self.assertEqual(set(metadata), set(payload["results"][0]["adme_predictions"]))
        self.assertEqual(metadata["aqueous_solubility_logS"]["units"], "log10(mol/L)")
        self.assertEqual(payload["descriptor_units"]["molecular_weight"], "g/mol")
        self.assertEqual(payload["descriptor_units"]["tpsa"], "angstrom^2")
        self.assertFalse(payload["interpretation"]["scores_are_probabilities"])
        self.assertFalse(payload["interpretation"]["outputs_are_measurements"])
        self.assertTrue(payload["method"]["rdkit_version"])
        self.assertEqual([r["adme_predictions"]["aqueous_solubility_logS"] for r in payload["results"]],
                         [-0.124228, -0.303222, -0.795396])
        for smiles, result in zip(["CCO", "CC(=O)O", "CC(=O)C"], payload["results"]):
            self.assertEqual(result, analyze_smiles(smiles).to_dict())

    def test_advertised_heuristic_formula_and_thresholds_reproduce_tool_outputs(self):
        # Includes an aromatic molecule, a polar sugar and a rounded 5/6 HIA score.
        payload = tool_profile_molecule_physchem_and_adme_heuristic(
            ["CCO", "c1ccccc1", "OCC(O)C(O)C(O)C(O)CO", "[Na+]"]
        )
        self.assertIn("property_metadata", payload)
        metadata = payload["property_metadata"]
        for row in payload["results"]:
            predictions = row["adme_predictions"]
            # Evaluate only expressions authored in our own metadata, with no builtins.
            context = {**row["descriptors"], "abs": abs}
            calculate = lambda text: eval(text, {"__builtins__": {}}, context)
            self.assertEqual(round(calculate(metadata["aqueous_solubility_logS"]["formula"]), 6),
                             predictions["aqueous_solubility_logS"])
            for field in ["human_intestinal_absorption", "caco2_permeability", "bbb_permeability"]:
                spec = metadata[field]
                score = round(sum(calculate(rule) for rule in spec["checks"]) / len(spec["checks"]), 6)
                self.assertEqual(score, predictions[spec["score_field"]])
                label = "high" if score >= spec["thresholds"]["high_min"] else (
                    "moderate" if score >= spec["thresholds"]["moderate_min"] else "low")
                self.assertEqual(label, predictions[field])
            violations = sum(calculate(rule) for rule in metadata["lipinski_violations"]["checks"])
            self.assertEqual(violations, predictions["lipinski_violations"])
            context["lipinski_violations"] = violations
            score = next(rule["value"] for rule in metadata["bioavailability_score"]["ordered_rules"]
                         if calculate(rule["condition"]))
            self.assertEqual(score, predictions["bioavailability_score"])

    def test_analyze_smiles_validates_and_calculates_descriptors_and_adme(self) -> None:
        analysis = analyze_smiles("CC(=O)Oc1ccccc1C(=O)O")

        self.assertTrue(analysis.is_valid)
        self.assertEqual(analysis.connectivity_smiles, "CC(=O)Oc1ccccc1C(=O)O")
        self.assertEqual(analysis.molecular_formula, "C9H8O4")
        self.assertAlmostEqual(analysis.molecular_weight or 0.0, 180.159, places=3)
        self.assertAlmostEqual(analysis.exact_mass or 0.0, 180.042259, places=6)
        self.assertAlmostEqual(float(analysis.descriptors["logp"]), 1.3101, places=4)
        self.assertAlmostEqual(float(analysis.descriptors["tpsa"]), 63.6, places=1)
        self.assertEqual(analysis.descriptors["hbd"], 1)
        self.assertEqual(analysis.descriptors["hba"], 3)
        self.assertEqual(analysis.descriptors["rotatable_bonds"], 2)
        self.assertEqual(analysis.descriptors["ring_count"], 1)
        self.assertEqual(analysis.descriptors["aromatic_ring_count"], 1)
        self.assertEqual(analysis.descriptors["heavy_atom_count"], 13)
        self.assertAlmostEqual(float(analysis.descriptors["fraction_csp3"]), 0.111111, places=6)

        self.assertAlmostEqual(float(analysis.adme_predictions["aqueous_solubility_logS"]), -2.104624, places=6)
        self.assertEqual(analysis.adme_predictions["human_intestinal_absorption"], "high")
        self.assertEqual(analysis.adme_predictions["bbb_permeability"], "high")
        self.assertEqual(analysis.adme_predictions["caco2_permeability"], "high")
        self.assertEqual(analysis.adme_predictions["bioavailability_score"], 0.55)
        self.assertEqual(analysis.adme_predictions["lipinski_violations"], 0)

    def test_analyze_smiles_raises_for_bad_structure(self) -> None:
        with self.assertRaisesRegex(ValueError, r"C1CC is invalid SMILES structure\."):
            analyze_smiles("C1CC")

    def test_connects_two_external_datasets_and_supports_exact_and_substructure_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "drugbank_like.csv"
            jsonl_path = Path(tmpdir) / "literature_hits.jsonl"

            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["compound_id", "compound_name", "smiles", "origin"])
                writer.writerow(["DB-1", "Aspirin", "CC(=O)Oc1ccccc1C(=O)O", "DrugBank"])
                writer.writerow(["DB-2", "Caffeine", "Cn1cnc2n(C)c(=O)n(C)c(=O)c12", "DrugBank"])

            with jsonl_path.open("w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "id": "LIT-1",
                            "name": "Benzene",
                            "smiles": "c1ccccc1",
                            "origin": "literature",
                        }
                    )
                    + "\n"
                )
                handle.write(
                    json.dumps(
                        {
                            "id": "LIT-2",
                            "name": "Acetaminophen",
                            "smiles": "CC(=O)NC1=CC=C(O)C=C1",
                            "origin": "literature",
                        }
                    )
                    + "\n"
                )

            collection = connect_smiles_datasets([csv_path, jsonl_path])

            self.assertIsInstance(collection, ConnectedMoleculeDataset)
            self.assertEqual(collection.summary()["dataset_count"], 2)
            self.assertEqual(collection.summary()["record_count"], 4)
            self.assertEqual(collection.summary()["valid_record_count"], 4)

            exact_matches = collection.search("CC(=O)Oc1ccccc1C(=O)O", match_type="exact")
            self.assertEqual(len(exact_matches), 1)
            self.assertEqual(exact_matches[0].compound_name, "Aspirin")
            self.assertTrue(exact_matches[0].source.label().endswith("drugbank_like::row 2::DB-1"))

            substructure_matches = collection.search("c1ccccc1", match_type="substructure")
            self.assertEqual([entry.compound_name for entry in substructure_matches], ["Aspirin", "Benzene", "Acetaminophen"])

    def test_dataset_loader_supports_configured_field_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "vendor_records.json"
            with json_path.open("w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "records": [
                            {
                                "external_id": "V-1",
                                "preferred_name": "Acetone",
                                "connectivity_smiles": "CC(=O)C",
                                "vendor": "catalog-a",
                            },
                        ]
                    },
                    handle,
                )

            collection = connect_smiles_datasets(
                [
                    SmilesDatasetSource(
                        file_path=json_path,
                        dataset_name="vendor_catalog",
                        smiles_field="connectivity_smiles",
                        id_field="external_id",
                        name_field="preferred_name",
                    )
                ]
            )

            self.assertEqual(collection.summary()["dataset_count"], 1)
            self.assertEqual(collection.summary()["record_count"], 1)
            self.assertEqual(collection.summary()["valid_record_count"], 1)
            self.assertEqual(collection.summary()["invalid_record_count"], 0)
            self.assertEqual(collection.entries[0].compound_name, "Acetone")
            self.assertEqual(collection.entries[0].metadata["vendor"], "catalog-a")
            self.assertTrue(collection.entries[0].source.label().endswith("vendor_records.json::vendor_catalog::row 1::V-1"))

    def test_dataset_loader_raises_for_invalid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "vendor_records.json"
            with json_path.open("w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "records": [
                            {
                                "external_id": "V-1",
                                "preferred_name": "Broken entry",
                                "connectivity_smiles": "C1CC",
                            },
                        ]
                    },
                    handle,
                )

            with self.assertRaisesRegex(
                MolecularStructureError,
                r"vendor_records\.json.*invalid SMILES 'C1CC'.*row 1",
            ):
                connect_smiles_datasets(
                    [
                        SmilesDatasetSource(
                            file_path=json_path,
                            dataset_name="vendor_catalog",
                            smiles_field="connectivity_smiles",
                            id_field="external_id",
                            name_field="preferred_name",
                        )
                    ]
                )

    def test_dataset_loader_raises_for_missing_smiles_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "missing_smiles.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["id", "name", "formula"])
                writer.writerow(["X-1", "Water", "H2O"])

            with self.assertRaises(MolecularStructureError):
                connect_smiles_datasets([csv_path])

    def test_search_validates_query_and_match_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "library.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["id", "name", "smiles"])
                writer.writerow(["L-1", "Ethanol", "CCO"])

            collection = connect_smiles_datasets([csv_path])

            with self.assertRaises(ValueError):
                collection.search("C1CC", match_type="exact")
            with self.assertRaises(ValueError):
                collection.search("CCO", match_type="similarity")  # type: ignore[arg-type]

    def test_build_research_report_enriches_scientific_content_and_confirms_hypothesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "screening.csv"
            json_path = Path(tmpdir) / "reference_set.json"

            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["compound_id", "compound_name", "smiles", "series"])
                writer.writerow(["SER-1", "Aspirin", "CC(=O)Oc1ccccc1C(=O)O", "salicylates"])

            with json_path.open("w", encoding="utf-8") as handle:
                json.dump(
                    [
                        {
                            "id": "REF-1",
                            "name": "Phenol",
                            "smiles": "Oc1ccccc1",
                            "series": "aromatics",
                        }
                    ],
                    handle,
                )

            collection = connect_smiles_datasets([csv_path, json_path])
            criteria = {
                "molecular_weight": {"max": 250},
                "human_intestinal_absorption": {"equals": "high"},
                "bioavailability_score": {"min": 0.5},
            }

            report = collection.build_research_report(
                "CC(=O)Oc1ccccc1C(=O)O",
                criteria=criteria,
                hypothesis_name="oral lead filter",
            )

            self.assertEqual(report["dataset_summary"]["dataset_count"], 2)
            self.assertEqual(len(report["exact_matches"]), 1)
            self.assertEqual(report["hypothesis_assessment"]["hypothesis_name"], "oral lead filter")
            self.assertTrue(report["hypothesis_assessment"]["supported"])
            self.assertEqual(report["hypothesis_assessment"]["satisfied_count"], 3)
            self.assertIn("screening.csv::screening::row 2::SER-1", report["scientific_summary"])
            self.assertIn("bioavailability score 0.55", report["scientific_summary"])
            self.assertIn("Hypothesis checks for oral lead filter satisfied 3/3", report["scientific_summary"])

    def test_scientific_summary_supports_sections_and_rejects_unknown_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "summary_dataset.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["id", "name", "smiles"])
                writer.writerow(["S-1", "Aspirin", "CC(=O)Oc1ccccc1C(=O)O"])

            collection = connect_smiles_datasets([csv_path])
            article_summary = collection.build_scientific_summary(
                "CC(=O)Oc1ccccc1C(=O)O",
                section="article",
            )
            hypothesis_summary = collection.build_scientific_summary(
                "CC(=O)Oc1ccccc1C(=O)O",
                section="hypothesis",
            )

            self.assertIn("manuscript discussion", article_summary)
            self.assertIn("lead-selection hypotheses", hypothesis_summary)
            with self.assertRaises(ValueError):
                collection.build_scientific_summary("CC(=O)Oc1ccccc1C(=O)O", section="notes")  # type: ignore[arg-type]

    def test_tool_wrappers_expose_analysis_search_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            inputs = workspace / "session-a" / "inputs"
            inputs.mkdir(parents=True)
            csv_path = inputs / "collection.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["id", "name", "smiles"])
                writer.writerow(["C-1", "Caffeine", "Cn1cnc2n(C)c(=O)n(C)c(=O)c12"])

            analysis_payload = tool_profile_molecule_physchem_and_adme_heuristic("Cn1cnc2n(C)c(=O)n(C)c(=O)c12")
            with patch.dict(os.environ, {"WORKSPACE_DIR": str(workspace)}):
                search_payload = tool_search_connected_datasets(
                    ["inputs/collection.csv"],
                    session_id="session-a",
                    query_smiles="Cn1cnc2n(C)c(=O)n(C)c(=O)c12",
                    match_type="exact",
                )
                report_payload = tool_build_structure_research_report(
                    ["inputs/collection.csv"],
                    session_id="session-a",
                    query_smiles="Cn1cnc2n(C)c(=O)n(C)c(=O)c12",
                    criteria={"molecular_weight": {"max": 250}},
                    hypothesis_name="small heterocycle filter",
                )

            self.assertTrue(analysis_payload["ok"])
            self.assertTrue({"ok", "results"}.issubset(analysis_payload))
            self.assertEqual(len(analysis_payload["results"]), 1)
            self.assertTrue(analysis_payload["results"][0]["is_valid"])
            self.assertEqual(analysis_payload["results"][0]["molecular_formula"], "C8H10N4O2")

            self.assertTrue(search_payload["ok"])
            self.assertTrue({"ok", "results", "sources", "partial", "truncated"}.issubset(search_payload))
            self.assertFalse(search_payload["partial"])
            self.assertEqual(len(search_payload["results"]), 1)
            self.assertEqual(search_payload["results"][0]["compound_name"], "Caffeine")

            self.assertTrue(report_payload["ok"])
            self.assertEqual(report_payload["report"]["hypothesis_assessment"]["satisfied_count"], 1)
            self.assertIn("small heterocycle filter", report_payload["report"]["scientific_summary"])

    def test_tool_wrappers_return_error_payloads_for_invalid_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            inputs = workspace / "session-a" / "inputs"
            inputs.mkdir(parents=True)
            csv_path = inputs / "tool_errors.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["id", "name", "smiles"])
                writer.writerow(["T-1", "Ethanol", "CCO"])

            blank_smiles_payload = tool_profile_molecule_physchem_and_adme_heuristic(" ")
            with patch.dict(os.environ, {"WORKSPACE_DIR": str(workspace)}):
                invalid_query_payload = tool_search_connected_datasets(
                    ["inputs/tool_errors.csv"],
                    session_id="session-a",
                    query_smiles="C1CC",
                )
                invalid_criteria_payload = tool_build_structure_research_report(
                    ["inputs/tool_errors.csv"],
                    session_id="session-a",
                    query_smiles="CCO",
                    criteria={"molecular_weight": {"gt": 10}},
                )

            self.assertFalse(blank_smiles_payload["ok"])
            self.assertEqual(set(blank_smiles_payload), {"ok", "error_type", "error"})
            self.assertEqual(blank_smiles_payload["error_type"], "ValueError")
            self.assertFalse(invalid_query_payload["ok"])
            self.assertEqual(set(invalid_query_payload), {"ok", "error_type", "error"})
            self.assertEqual(invalid_query_payload["error_type"], "ValueError")
            self.assertIn("is invalid SMILES structure", invalid_query_payload["error"])
            self.assertFalse(invalid_criteria_payload["ok"])
            self.assertEqual(set(invalid_criteria_payload), {"ok", "error_type", "error"})
            self.assertEqual(invalid_criteria_payload["error_type"], "ValueError")
            self.assertIn("Unsupported criterion operator", invalid_criteria_payload["error"])


if __name__ == "__main__":
    unittest.main()
