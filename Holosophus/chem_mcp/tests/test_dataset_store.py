"""Dataset registration and durable snapshots, without model or network calls."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import yaml

from dataset_registry import DatasetRegistry
from dataset_store import DatasetStore, DatasetStoreError


class DatasetStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "input.csv"
        self.source.write_text("smiles,compound_id,value\nCCO,A,1.5\nCC,B,2.5\n")
        self.config = self.root / "datasets.yaml"
        self.definition = {
            "version": 1,
            "datasets": {
                "example": {
                    "title": "Пример",
                    "description": "Тестовая таблица, значения в мг/л.",
                    "source": {"type": "file", "path": "input.csv"},
                    "preload": True,
                    "operations": ["sql", "structure"],
                    "smiles_field": "smiles",
                    "id_field": "compound_id",
                    "schema": [{"name": name} for name in ("smiles", "compound_id", "value")],
                    "example_rows": [{"smiles": "CCO", "compound_id": "A", "value": 1.5}],
                }
            },
        }
        self.save_config()

    def save_config(self):
        self.config.write_text(yaml.safe_dump(self.definition, allow_unicode=True))
        self.registry = DatasetRegistry(self.config)

    def store(self, **kwargs):
        return DatasetStore(self.registry, self.root / "stored", **kwargs)

    def test_default_catalog_preserves_ten_sources_and_preloads_only_two(self):
        registry = DatasetRegistry()
        self.assertEqual(len(registry.specs), 10)
        self.assertEqual(
            {s.key for s in registry.specs if s.preload}, {"benzimidazoles", "oxazolidinones"}
        )
        self.assertEqual(registry.resolve("ai-chem/Co-crystals").key, "co_crystals")
        for spec in registry.specs:
            self.assertTrue(spec.schema)

    def test_describe_before_loading_is_offline_and_does_not_create_storage(self):
        with patch("dataset_store.load_source", side_effect=AssertionError("must not load")):
            listing = self.registry.list_datasets()
            description = self.store().describe("example")
        self.assertEqual(listing[0]["dataset_id"], "example")
        self.assertNotIn("preload", listing[0])
        self.assertNotIn("local", listing[0])
        self.assertEqual(description["metadata_origin"], "registered")
        self.assertIsNone(description["row_count"])
        self.assertEqual(
            [c["name"] for c in description["schema"]], ["smiles", "compound_id", "value"]
        )
        self.assertFalse((self.root / "stored").exists())

    def test_invalid_id_and_mapping_fail_at_registration(self):
        for field, value in (
            ("smiles_field", "missing"),
            ("operations", ["shell"]),
            ("preload", "yes"),
        ):
            with self.subTest(field=field):
                entry = self.definition["datasets"]["example"]
                old = entry[field]
                entry[field] = value
                self.config.write_text(yaml.safe_dump(self.definition))
                with self.assertRaises(ValueError):
                    DatasetRegistry(self.config)
                entry[field] = old
        self.definition["datasets"]["../outside"] = self.definition["datasets"].pop("example")
        self.config.write_text(yaml.safe_dump(self.definition))
        with self.assertRaises(ValueError):
            DatasetRegistry(self.config)

    def test_duplicate_yaml_ids_are_not_silently_overwritten(self):
        self.config.write_text("version: 1\ndatasets:\n  repeated: {}\n  repeated: {}\n")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            DatasetRegistry(self.config)

    def test_unknown_dataset_does_not_touch_storage(self):
        with self.assertRaisesRegex(ValueError, "Unknown dataset"):
            self.store().prepare("unknown")
        self.assertFalse((self.root / "stored").exists())

    def test_snapshot_can_be_read_in_new_process_after_original_is_removed(self):
        snapshot = self.store().prepare("example")
        self.assertEqual(snapshot.metadata["row_count"], 2)
        self.source.unlink()
        script = (
            "from dataset_registry import DatasetRegistry; from dataset_store import DatasetStore; "
            "import sys; s=DatasetStore(DatasetRegistry(sys.argv[1]), sys.argv[2]); "
            "f=s.load_frame('example'); print(len(f), f.iloc[0]['smiles'])"
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.config), str(self.root / "stored")],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "2 CCO")
        self.assertEqual(self.store().describe("example")["metadata_origin"], "snapshot")

    def test_refresh_is_explicit_and_old_snapshot_survives(self):
        store = self.store()
        first = store.prepare("example")
        self.source.write_text("smiles,compound_id,value\nCCC,B,3.5\n")
        self.assertEqual(store.prepare("example").version, first.version)
        second = store.prepare("example", refresh=True)
        self.assertNotEqual(second.version, first.version)
        self.assertTrue(first.records_path.exists())
        self.assertEqual(store.load_frame("example").iloc[0]["smiles"], "CCC")

    def test_failed_refresh_keeps_previous_version(self):
        store = self.store()
        first = store.prepare("example")
        self.source.write_text("wrong_column\nvalue\n")
        with self.assertRaises(DatasetStoreError):
            store.prepare("example", refresh=True)
        self.assertEqual(store.prepare("example").version, first.version)
        self.assertEqual(len(store.load_frame("example")), 2)

    def test_corrupt_snapshot_is_reported_instead_of_silent_download(self):
        snapshot = self.store().prepare("example")
        snapshot.records_path.write_text('{"smiles":"wrong"}\n')
        with patch("dataset_store.load_source", side_effect=AssertionError("must not reload")):
            with self.assertRaisesRegex(DatasetStoreError, "checksum"):
                self.store().load_frame("example")

    def test_source_change_requires_explicit_refresh(self):
        self.store().prepare("example")
        self.definition["datasets"]["example"]["source"]["path"] = "different.csv"
        self.save_config()
        with self.assertRaisesRegex(DatasetStoreError, "refresh"):
            self.store().prepare("example")

    def test_json_strings_and_nulls_survive_roundtrip(self):
        self.source = self.root / "input.json"
        self.source.write_text(
            json.dumps(
                [
                    {"smiles": "CCO", "compound_id": "001", "value": "5,52", "extra": None},
                    {"smiles": "CC", "compound_id": "002", "value": "64", "extra": "NA"},
                ]
            )
        )
        self.definition["datasets"]["example"]["source"]["path"] = "input.json"
        self.save_config()
        frame = self.store().load_frame("example")
        self.assertEqual(frame.iloc[0]["compound_id"], "001")
        self.assertEqual(frame.iloc[1]["value"], "64")
        self.assertTrue(pd.isna(frame.iloc[0]["extra"]))
        self.assertEqual(frame.iloc[1]["extra"], "NA")
        self.assertEqual(self.store().describe("example")["schema_changes"]["added"], ["extra"])

    def test_jsonl_and_csv_use_paths_relative_to_registry(self):
        records = [{"smiles": "CCO", "compound_id": "A", "value": "0.5"}]
        (self.root / "input.jsonl").write_text(json.dumps(records[0]) + "\n")
        self.definition["datasets"]["example"]["source"]["path"] = "input.jsonl"
        self.save_config()
        self.assertEqual(self.store().load_frame("example").iloc[0]["value"], "0.5")

    def test_preload_ignores_other_registered_sources(self):
        self.definition["datasets"]["unused"] = {
            **self.definition["datasets"]["example"],
            "source": {"type": "file", "path": "missing.csv"},
            "preload": False,
        }
        self.save_config()
        result = self.store().preload()
        self.assertEqual([s.metadata["dataset_id"] for s in result], ["example"])
        self.assertFalse((self.root / "stored" / "unused").exists())

    def test_huggingface_loader_resolves_revision_once_and_reuses_snapshot(self):
        self.definition["datasets"]["example"]["source"] = {
            "type": "huggingface",
            "repo_id": "test/molecules",
            "config": "default",
            "split": "train",
        }
        self.save_config()
        fake_split = type(
            "Split",
            (),
            {
                "to_pandas": lambda _: pd.DataFrame(
                    [
                        {"smiles": "CCO", "compound_id": "A", "value": "64"},
                    ]
                )
            },
        )()
        with (
            patch("huggingface_hub.HfApi.dataset_info") as info,
            patch("datasets.load_dataset", return_value=fake_split) as load,
        ):
            info.return_value.sha = "a" * 40
            snapshot = self.store().prepare("example")
            self.store().load_frame("example")
        load.assert_called_once_with(
            "test/molecules", name="default", split="train", revision="a" * 40
        )
        self.assertEqual(snapshot.metadata["source"]["resolved_revision"], "a" * 40)

    def test_cli_prepares_data_without_starting_mcp(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "prepare_datasets",
                "--registry",
                str(self.config),
                "--data-dir",
                str(self.root / "stored"),
                "--preload",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout)["prepared"][0]["row_count"], 2)

    def test_two_simultaneous_first_reads_prepare_only_once(self):
        from dataset_store import load_source

        with patch("dataset_store.load_source", wraps=load_source) as loader:
            with ThreadPoolExecutor(max_workers=2) as executor:
                versions = list(
                    executor.map(lambda _: self.store().prepare("example").version, range(2))
                )
        self.assertEqual(versions[0], versions[1])
        self.assertEqual(loader.call_count, 1)

    def test_refresh_repairs_corrupt_metadata(self):
        snapshot = self.store().prepare("example")
        snapshot.records_path.with_name("metadata.json").write_text("broken")
        repaired = self.store().prepare("example", refresh=True)
        self.assertEqual(repaired.version, snapshot.version)
        self.assertEqual(self.store().describe("example")["row_count"], 2)

    def test_existing_sql_client_reads_store_and_observes_refresh(self):
        from chemx_integration import ChemXClient

        store = self.store()
        client = ChemXClient(store=store)
        self.assertEqual(
            client.execute_sql("example", "SELECT COUNT(*) AS n FROM dataset")["rows"], [{"n": 2}]
        )
        self.source.write_text("smiles,compound_id,value\nCCC,C,3.5\n")
        store.prepare("example", refresh=True)
        self.source.unlink()
        self.assertEqual(
            client.execute_sql("example", "SELECT row_idx, smiles FROM dataset")["rows"],
            [{"row_idx": 0, "smiles": "CCC"}],
        )

    def test_csv_keeps_molecular_identifiers_as_text(self):
        self.source.write_text("smiles,compound_id,value\nNA,001,1.5\n")
        row = self.store().load_frame("example").iloc[0]
        self.assertEqual(row["smiles"], "NA")
        self.assertEqual(row["compound_id"], "001")


if __name__ == "__main__":
    unittest.main()
