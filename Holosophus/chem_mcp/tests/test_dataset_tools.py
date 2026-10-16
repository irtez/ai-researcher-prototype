"""Shared dataset tools: actual SQL/RDKit over local fixtures, no network or LLM."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

import chemx_tools
from chemx_integration import ChemXClient
from dataset_registry import DatasetRegistry
from dataset_store import DatasetStore
from dataset_tools import tool_list_datasets, tool_describe_dataset, tool_query_dataset_sql
from molecular_structure_tools import (
    tool_search_connected_datasets,
    tool_build_structure_research_report,
)


class DatasetToolTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.workspace = self.root / "sessions"
        inputs = self.workspace / "session-a" / "inputs"
        inputs.mkdir(parents=True)
        (inputs / "sample.json").write_text(json.dumps([{"structure": "OCC", "code": "G1"}]))
        self.records = [{"smiles": "CC", "compound_id": f"A-{i}", "value": i} for i in range(120)]
        self.records += [
            {"smiles": "CCO", "compound_id": "A-120", "value": 120},
            {"smiles": "C1CC", "compound_id": "bad1", "value": None},
            {"smiles": "", "compound_id": "bad2", "value": None},
        ]
        entries = {}
        for key, rows in (
            ("alpha", self.records),
            (
                "beta",
                [
                    {"smiles": "CCO", "compound_id": "B1", "value": "5,52"},
                    {"smiles": "c1ccccc1", "compound_id": "B2", "value": "64"},
                ],
            ),
        ):
            (self.root / f"{key}.json").write_text(json.dumps(rows))
            entries[key] = {
                "title": key,
                "description": "Molecular test table",
                "operations": ["sql", "structure"],
                "source": {"type": "file", "path": f"{key}.json"},
                "smiles_field": "smiles",
                "id_field": "compound_id",
                "schema": [{"name": c} for c in ("smiles", "compound_id", "value")],
            }
        config = self.root / "datasets.yaml"
        config.write_text(yaml.safe_dump({"version": 1, "datasets": entries}))
        self.store = DatasetStore(DatasetRegistry(config), self.root / "stored")
        self.client = ChemXClient(store=self.store)
        self.addCleanup(patch.stopall)
        patch("dataset_tools._get_client", return_value=self.client).start()
        patch.dict(os.environ, {"WORKSPACE_DIR": str(self.workspace)}).start()

    def search(self, sources=None, **kwargs):
        return tool_search_connected_datasets(
            sources if sources is not None else [{"dataset_id": "alpha"}],
            session_id="session-a",
            query_smiles=kwargs.pop("query_smiles", "OCC"),
            **kwargs,
        )

    def test_list_and_describe_are_offline_before_loading(self):
        with patch("dataset_store.load_source", side_effect=AssertionError("must not load")):
            listing = tool_list_datasets()
            description = tool_describe_dataset("alpha")
        self.assertEqual([s["dataset_id"] for s in listing["datasets"]], ["alpha", "beta"])
        self.assertNotIn("schema", listing["datasets"][0])
        self.assertTrue(description["ok"])
        self.assertEqual(description["metadata_origin"], "registered")
        self.assertEqual(description["table_name"], "dataset")
        self.assertIn("row_idx", [c["name"] for c in description["schema"]])
        self.assertFalse((self.root / "stored").exists())

    def test_sql_result_preserves_limit_and_snapshot_identity(self):
        result = tool_query_dataset_sql("alpha", "SELECT row_idx, smiles FROM dataset")
        self.assertTrue(result["ok"])
        self.assertEqual(result["returned_rows"], 100)
        self.assertTrue(result["truncated"])
        self.assertIsNone(result["row_count"])
        self.assertEqual(result["source"]["dataset_id"], "alpha")
        self.assertEqual(result["source"]["row_count"], 123)
        self.assertEqual(result["source"]["version"], self.store.prepare("alpha").version)
        self.assertEqual(result["rows"][0]["row_idx"], 0)

    def test_sql_limit_is_a_limit_on_the_query_not_total_dataset_size(self):
        result = tool_query_dataset_sql("alpha", "SELECT smiles FROM dataset LIMIT 2")
        self.assertEqual(result["row_count"], 2)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["source"]["row_count"], 123)

    def test_sql_supports_cte_and_aggregates_without_mutating_source(self):
        result = tool_query_dataset_sql(
            "alpha",
            "WITH candidates AS (SELECT * FROM dataset WHERE row_idx < 120) SELECT COUNT(*) AS n FROM candidates",
        )
        self.assertEqual(result["rows"], [{"n": 120}])

    def test_sql_rejects_writes_attach_pragma_and_extensions(self):
        snapshot = self.store.prepare("alpha")
        original = snapshot.records_path.read_bytes()
        destination = self.root / "must-not-exist.sqlite"
        for query in (
            "DELETE FROM dataset",
            "/* test */ UPDATE dataset SET smiles = 'N'",
            "CREATE TABLE unwanted(x)",
            "DROP TABLE dataset",
            f"ATTACH DATABASE '{destination}' AS other",
            "PRAGMA journal_mode=OFF",
            "SELECT load_extension('unwanted')",
            "SELECT * FROM sqlite_master",
            "SELECT 1; SELECT 2",
            "-- only a comment",
        ):
            with self.subTest(query=query):
                result = tool_query_dataset_sql("alpha", query)
                self.assertFalse(result["ok"], result)
        self.assertFalse(destination.exists())
        self.assertEqual(snapshot.records_path.read_bytes(), original)

    def test_sql_invalid_limits_do_not_load_data(self):
        for limit in (0, -1, 1001, True):
            with self.subTest(limit=limit):
                with patch(
                    "dataset_store.load_source", side_effect=AssertionError("must not load")
                ):
                    self.assertFalse(
                        tool_query_dataset_sql("alpha", "SELECT * FROM dataset", row_limit=limit)[
                            "ok"
                        ]
                    )

    def test_search_finds_match_after_first_hundred_records(self):
        result = self.search()
        self.assertTrue(result["ok"])
        self.assertEqual(result["returned_rows"], 1)
        self.assertEqual(result["results"][0]["source"]["row_idx"], 120)
        self.assertEqual(result["results"][0]["source"]["record_id"], "A-120")
        self.assertEqual(result["results"][0]["source"]["dataset_id"], "alpha")
        self.assertTrue(result["partial"])
        self.assertEqual(result["sources"][0]["skipped_records"], 2)

    def test_both_datasets_and_file_use_same_search_and_keep_origin(self):
        result = self.search(
            [
                {"dataset_id": "alpha"},
                {"dataset_id": "beta"},
                {
                    "file_path": "inputs/sample.json",
                    "smiles_field": "structure",
                    "id_field": "code",
                },
            ],
            match_type="substructure",
            query_smiles="CO",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["matched_rows"], 3)
        self.assertEqual(len(result["sources"]), 3)
        self.assertEqual(
            [r["source"]["record_id"] for r in result["results"]], ["A-120", "B1", "G1"]
        )
        self.assertEqual(result["results"][-1]["source"]["file_path"], "inputs/sample.json")

    def test_limit_does_not_prevent_checking_second_source(self):
        result = self.search([{"dataset_id": "alpha"}, {"dataset_id": "beta"}], max_results=1)
        self.assertEqual(result["returned_rows"], 1)
        self.assertEqual(result["matched_rows"], 2)
        self.assertTrue(result["truncated"])
        self.assertEqual([s["matched_rows"] for s in result["sources"]], [1, 1])

    def test_bad_query_and_mode_are_rejected_before_loading(self):
        with patch("dataset_store.load_source", side_effect=AssertionError("must not load")):
            for kwargs in ({"query_smiles": "C1CC"}, {"match_type": "unknown"}, {"max_results": 0}):
                self.assertFalse(self.search(**kwargs)["ok"])

    def test_partial_failure_is_not_reported_as_no_matches(self):
        result = self.search([{"dataset_id": "beta"}, {"dataset_id": "missing"}])
        self.assertTrue(result["ok"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["returned_rows"], 1)
        self.assertEqual(result["sources"][1]["status"], "error")
        self.assertIn("Unknown dataset", result["sources"][1]["error"])
        failed = self.search([{"dataset_id": "missing"}])
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["sources"][0]["status"], "error")

    def test_ambiguous_sources_and_paths_outside_session_are_rejected(self):
        outside = self.workspace / "session-b"
        outside.mkdir()
        (outside / "secret.json").write_text('[{"smiles":"CCO","id":"PRIVATE"}]')
        (self.workspace / "session-a" / "escape").symlink_to(outside, target_is_directory=True)
        for source in (
            {"dataset_id": "alpha", "file_path": "inputs/sample.json"},
            {"dataset_id": "alpha", "smiles_field": "untrusted"},
            {"file_path": str(outside / "secret.json")},
            "../session-b/secret.json",
            "escape/secret.json",
        ):
            with self.subTest(source=source):
                result = self.search([source])
                self.assertFalse(result["ok"])
                self.assertNotIn('"PRIVATE"', json.dumps(result))

    def test_report_still_works_for_session_files(self):
        result = tool_build_structure_research_report(
            [{"file_path": "inputs/sample.json", "smiles_field": "structure", "id_field": "code"}],
            session_id="session-a",
            query_smiles="CCO",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["report"]["exact_matches"][0]["source"]["record_id"], "G1")

    def test_empty_session_dataset_is_not_an_error(self):
        (self.workspace / "session-a" / "inputs" / "empty.json").write_text("[]")
        result = self.search(["inputs/empty.json"])
        self.assertTrue(result["ok"])
        self.assertFalse(result["partial"])
        self.assertEqual(result["matched_rows"], 0)

    def test_wrong_field_mapping_is_a_source_error(self):
        result = self.search([{"file_path": "inputs/sample.json", "smiles_field": "absent"}])
        self.assertFalse(result["ok"])
        self.assertEqual(result["sources"][0]["status"], "error")
        self.assertIn("Configured smiles field", result["sources"][0]["error"])

    def test_malformed_csv_has_a_source_error_instead_of_crashing(self):
        (self.workspace / "session-a" / "inputs" / "bad.csv").write_text("smiles\nCCO,extra\n")
        result = self.search(["inputs/bad.csv"])
        self.assertFalse(result["ok"])
        self.assertIn("row 2", result["sources"][0]["error"])

    def test_unsupported_operation_does_not_load_data(self):
        from dataclasses import replace

        spec = self.store.registry.resolve("alpha")
        with patch.object(
            self.store.registry, "resolve", return_value=replace(spec, operations=("sql",))
        ):
            self.assertFalse(self.search()["ok"])
        with patch.object(
            self.store.registry, "resolve", return_value=replace(spec, operations=("structure",))
        ):
            self.assertFalse(tool_query_dataset_sql("alpha", "SELECT 1")["ok"])
        self.assertFalse((self.root / "stored").exists())

    def test_search_and_sql_refer_to_same_version(self):
        sql = tool_query_dataset_sql(
            "beta", "SELECT row_idx, compound_id FROM dataset WHERE smiles = 'CCO'"
        )
        search = self.search([{"dataset_id": "beta"}])
        self.assertEqual(search["results"][0]["source"]["version"], sql["source"]["version"])
        self.assertEqual(search["results"][0]["source"]["row_idx"], sql["rows"][0]["row_idx"])

    def test_sql_query_budget_interrupts_expensive_recursive_aggregation(self):
        self.store.prepare("alpha")
        with patch("chemx_integration.SQL_TIMEOUT_SECONDS", -1):
            result = tool_query_dataset_sql(
                "alpha",
                "WITH RECURSIVE t(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM t WHERE x<1000000) SELECT sum(x) FROM t",
            )
        self.assertFalse(result["ok"])
        self.assertIn("interrupted", result["error"])

    def test_mcp_roundtrip_uses_production_registry_and_mixed_source_schema(self):
        import asyncio
        from datetime import timedelta
        import sys
        import types
        from unittest.mock import Mock
        from mcp.shared.memory import create_connected_server_and_client_session

        # The local test environment omits the unrelated neural model dependency.
        # Registration and the exercised dataset tools are the actual production code.
        optional_model = types.ModuleType("admet_ai")
        optional_model.ADMETModel = Mock(side_effect=AssertionError("NN model must not run"))
        with patch.dict(sys.modules, {"admet_ai": optional_model}):
            from mcp_server import build_mcp_server

            server = build_mcp_server()

        async def roundtrip():
            async with create_connected_server_and_client_session(
                server, read_timeout_seconds=timedelta(seconds=15)
            ) as client:
                listing = await client.list_tools()
                names = {tool.name for tool in listing.tools}
                self.assertTrue(
                    {
                        "list_datasets",
                        "describe_dataset",
                        "query_dataset_sql",
                        "search_connected_datasets",
                    }.issubset(names)
                )
                self.assertFalse(any(name.startswith("search_chemx_") for name in names))
                # A resumed old conversation may still issue a known legacy call.
                # Keep its handler, but don't advertise ten duplicate SQL tools.
                config = yaml.safe_load((self.root / "datasets.yaml").read_text())
                config["datasets"]["benzimidazoles"] = config["datasets"]["alpha"]
                legacy_config = self.root / "legacy.yaml"
                legacy_config.write_text(yaml.safe_dump(config))
                legacy_store = DatasetStore(DatasetRegistry(legacy_config), self.root / "legacy-data")
                with patch.object(chemx_tools, "_build_client", return_value=ChemXClient(store=legacy_store)):
                    legacy = await client.call_tool(
                        "search_chemx_benzimidazoles", {"sql_query": "SELECT COUNT(*) AS n FROM dataset"}
                    )
                self.assertFalse(legacy.isError)
                self.assertTrue(legacy.structuredContent["ok"], legacy.structuredContent)
                self.assertEqual(legacy.structuredContent["rows"], [{"n": 123}])
                description = await client.call_tool("describe_dataset", {"dataset_id": "beta"})
                self.assertEqual(description.structuredContent["metadata_origin"], "registered")
                (self.workspace / "session-a" / "inputs" / "standard.json").write_text(
                    '[{"smiles":"OCC"}]'
                )
                result = await client.call_tool(
                    "search_connected_datasets",
                    {
                        "sources": [{"dataset_id": "beta"}, "inputs/standard.json"],
                        "session_id": "session-a",
                        "query_smiles": "CCO",
                    },
                )
                self.assertFalse(result.isError)
                self.assertEqual(result.structuredContent["returned_rows"], 2)
                sql = await client.call_tool(
                    "query_dataset_sql",
                    {"dataset_id": "beta", "sql_query": "SELECT COUNT(*) AS n FROM dataset"},
                )
                self.assertEqual(sql.structuredContent["rows"], [{"n": 2}])

        asyncio.run(roundtrip())
        optional_model.ADMETModel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
