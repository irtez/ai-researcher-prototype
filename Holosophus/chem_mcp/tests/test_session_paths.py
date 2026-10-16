import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from file_tools import tool_read_data_file
from molecular_structure_tools import tool_search_connected_datasets


class SessionPathToolTests(unittest.TestCase):
    def test_read_data_file_accepts_unicode_alphanumeric_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            session_id = "research__проверка_таблица__sonnet__20260907003245"
            inputs = workspace / session_id / "inputs"
            inputs.mkdir(parents=True)
            (inputs / "measurements.csv").write_text(
                "compound,value\nethanol,42\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"WORKSPACE_DIR": str(workspace)}):
                payload = tool_read_data_file("measurements.csv", session_id)

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["tables"], ["compound,value\nethanol,42\n"])

    def test_read_data_file_reads_only_from_session_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            inputs = workspace / "session-a" / "inputs"
            inputs.mkdir(parents=True)
            (inputs / "measurements.csv").write_text(
                "compound,value\nethanol,42\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"WORKSPACE_DIR": str(workspace)}):
                payload = tool_read_data_file("measurements.csv", "session-a")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["tables"], ["compound,value\nethanol,42\n"])

    def test_read_data_file_rejects_session_and_file_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            other_inputs = workspace / "session-b" / "inputs"
            other_inputs.mkdir(parents=True)
            (other_inputs / "secret.csv").write_text("secret,value\nx,1\n", encoding="utf-8")

            with patch.dict(os.environ, {"WORKSPACE_DIR": str(workspace)}):
                bad_session = tool_read_data_file("secret.csv", "../session-b")
                foreign_file = tool_read_data_file(
                    "../session-b/inputs/secret.csv",
                    "session-a",
                )

            self.assertFalse(bad_session["ok"])
            self.assertEqual(bad_session["error_type"], "ValueError")
            self.assertFalse(foreign_file["ok"])
            self.assertEqual(foreign_file["error_type"], "ValueError")

    def test_read_data_file_rejects_session_symlink_to_another_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            other_inputs = workspace / "session-b" / "inputs"
            other_inputs.mkdir(parents=True)
            (other_inputs / "secret.csv").write_text("secret,value\nx,1\n", encoding="utf-8")
            (workspace / "session-a").symlink_to(workspace / "session-b", target_is_directory=True)

            with patch.dict(os.environ, {"WORKSPACE_DIR": str(workspace)}):
                payload = tool_read_data_file("secret.csv", "session-a")

            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error_type"], "ValueError")

    def test_read_data_file_rejects_inputs_symlink_to_another_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            session_dir = workspace / "session-a"
            session_dir.mkdir()
            other_inputs = workspace / "session-b" / "inputs"
            other_inputs.mkdir(parents=True)
            (other_inputs / "secret.csv").write_text("secret,value\nx,1\n", encoding="utf-8")
            (session_dir / "inputs").symlink_to(other_inputs, target_is_directory=True)

            with patch.dict(os.environ, {"WORKSPACE_DIR": str(workspace)}):
                payload = tool_read_data_file("secret.csv", "session-a")

            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error_type"], "ValueError")

    def test_dataset_source_is_resolved_inside_session_and_preserves_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            inputs = workspace / "session-a" / "inputs"
            inputs.mkdir(parents=True)
            csv_path = inputs / "custom.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["registry", "label", "structure", "series"])
                writer.writerow(["CMP-1", "Ethanol", "CCO", "alcohols"])

            source = {
                "file_path": "inputs/custom.csv",
                "dataset_name": "screening",
                "smiles_field": "structure",
                "id_field": "registry",
                "name_field": "label",
            }
            with patch.dict(os.environ, {"WORKSPACE_DIR": str(workspace)}):
                payload = tool_search_connected_datasets(
                    [source],
                    session_id="session-a",
                    query_smiles="CCO",
                )

            self.assertTrue(payload["ok"])
            result = payload["results"][0]
            self.assertEqual(result["source"]["dataset_name"], "screening")
            self.assertEqual(result["source"]["record_id"], "CMP-1")
            self.assertEqual(result["compound_name"], "Ethanol")
            self.assertEqual(result["metadata"], {"series": "alcohols"})

    def test_dataset_source_rejects_absolute_and_traversal_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            session_dir = workspace / "session-a"
            session_dir.mkdir(parents=True)

            with patch.dict(os.environ, {"WORKSPACE_DIR": str(workspace)}):
                absolute = tool_search_connected_datasets(
                    [str(workspace / "outside.csv")],
                    session_id="session-a",
                    query_smiles="CCO",
                )
                traversal = tool_search_connected_datasets(
                    ["../session-b/inputs/other.csv"],
                    session_id="session-a",
                    query_smiles="CCO",
                )

            self.assertFalse(absolute["ok"])
            self.assertEqual(absolute["error_type"], "ValueError")
            self.assertFalse(traversal["ok"])
            self.assertEqual(traversal["error_type"], "ValueError")

    def test_invalid_dataset_smiles_returns_source_and_row_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            inputs = workspace / "session-a" / "inputs"
            inputs.mkdir(parents=True)
            (inputs / "bad.csv").write_text(
                "id,smiles\nBAD-1,C1CC\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"WORKSPACE_DIR": str(workspace)}):
                payload = tool_search_connected_datasets(
                    ["inputs/bad.csv"],
                    session_id="session-a",
                    query_smiles="CCO",
                )

            self.assertTrue(payload["ok"])
            self.assertTrue(payload["partial"])
            self.assertEqual(payload["returned_rows"], 0)
            self.assertEqual(payload["sources"][0]["valid_records"], 0)
            self.assertEqual(payload["sources"][0]["skipped_records"], 1)
            error = payload["sources"][0]["skipped_examples"][0]["error"]
            self.assertIn("bad.csv", error)
            self.assertIn("row 2", error)
            self.assertIn("invalid SMILES", error)


if __name__ == "__main__":
    unittest.main()
