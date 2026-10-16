import unittest
from unittest.mock import patch

import pandas as pd

from chemx_integration import CHEMX_DATASET_SPECS, ChemXClient, ChemXIntegrationError, resolve_chemx_dataset
from chemx_tools import TOOL_REGISTRY, tool_search_chemx_co_crystals, tool_search_chemx_cytotox


class _FakeDatasetSplit:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._frame = pd.DataFrame(rows)

    @property
    def column_names(self) -> list[str]:
        return list(self._frame.columns)

    @property
    def num_rows(self) -> int:
        return len(self._frame)

    def to_pandas(self) -> pd.DataFrame:
        return self._frame.copy()


class _FakeChemXClient(ChemXClient):
    def __init__(self, rows_by_dataset: dict[str, list[dict[str, object]]]) -> None:
        super().__init__()
        self.rows_by_dataset = rows_by_dataset
        self.calls: list[str] = []

    def _load_dataset_split(self, spec):  # type: ignore[override]
        self.calls.append(spec.hf_dataset_id)
        return _FakeDatasetSplit(self.rows_by_dataset.get(spec.key, []))


class ChemXIntegrationTests(unittest.TestCase):
    def test_registry_exposes_ten_dataset_specific_tools_and_aliases(self) -> None:
        co_crystals = resolve_chemx_dataset("co-crystals")
        client = _FakeChemXClient(
            {
                "co_crystals": [
                    {
                        "SMILES_drug": "C",
                        "ratio_cocrystal_page.1": "2",
                        "name_coformer_type file": "table",
                    }
                ]
            }
        )
        context = client.execute_sql(
            "co-crystals",
            (
                'SELECT row_idx, SMILES_drug, "ratio_cocrystal_page.1", '
                '"name_coformer_type file" FROM dataset'
            ),
            row_limit=1,
        )
        schema_by_source = {
            column["source_name"]: column["sql_name"]
            for column in context["schema"]
            if column["source_name"] != "Hugging Face row index"
        }

        self.assertEqual(len(CHEMX_DATASET_SPECS), 10)
        self.assertIn("search_chemx_cytotox", TOOL_REGISTRY)
        self.assertIn("search_chemx_nanomag", TOOL_REGISTRY)
        self.assertEqual(co_crystals.key, "co_crystals")
        self.assertEqual(context["dataset"]["row_count"], 1)
        self.assertEqual(schema_by_source["SMILES_drug"], "SMILES_drug")
        self.assertEqual(schema_by_source["ratio_cocrystal_page.1"], "ratio_cocrystal_page.1")
        self.assertEqual(schema_by_source["name_coformer_type file"], "name_coformer_type file")

    def test_execute_sql_uses_source_column_names(self) -> None:
        rows = [
            {
                "sn": 0,
                "material": "SiO2",
                "shape": "Sphere",
                "test": "MTT",
                "viability_%": 113.67,
                "concentration": 1.95,
                "cell_type": "PC12",
            },
            {
                "sn": 1,
                "material": "ZnO",
                "shape": "Rod",
                "test": "MTT",
                "viability_%": 62.5,
                "concentration": 5.0,
                "cell_type": "A549",
            },
        ]
        client = _FakeChemXClient({"cytotox": rows})

        payload = client.execute_sql(
            "Cytotox",
            (
                'SELECT row_idx, material, "viability_%", concentration '
                'FROM dataset WHERE test = \'MTT\' AND "viability_%" < 100 '
                "ORDER BY concentration DESC"
            ),
            row_limit=5,
        )

        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(payload["returned_rows"], 1)
        self.assertEqual(payload["rows"][0]["row_idx"], 1)
        self.assertEqual(payload["rows"][0]["material"], "ZnO")
        self.assertEqual(payload["rows"][0]["viability_%"], 62.5)
        self.assertEqual(payload["dataset"]["row_count"], 2)
        self.assertIn(
            {"sql_name": "viability_%", "source_name": "viability_%"},
            payload["schema"],
        )

    def test_execute_sql_surfaces_sql_errors_from_pandas(self) -> None:
        client = _FakeChemXClient({"cytotox": [{"material": "SiO2"}]})

        with self.assertRaisesRegex(ChemXIntegrationError, "ChemX SQL query failed"):
            client.execute_sql("Cytotox", "SELECT missing_column FROM dataset")

    def test_tool_wrappers_return_structured_payloads_and_docstrings(self) -> None:
        rows = [
            {
                "sn": 0,
                "material": "SiO2",
                "shape": "Sphere",
                "test": "MTT",
                "viability_%": 113.67,
                "concentration": 1.95,
                "cell_type": "PC12",
            }
        ]
        fake_client = _FakeChemXClient({"cytotox": rows})

        with patch("chemx_tools._build_client", return_value=fake_client):
            success_payload = tool_search_chemx_cytotox(
                sql_query='SELECT row_idx, material, "viability_%" FROM dataset',
                row_limit=10,
            )
            error_payload = tool_search_chemx_cytotox(
                sql_query="SELECT missing_column FROM dataset",
                row_limit=10,
            )

        self.assertTrue(success_payload["ok"])
        self.assertTrue({"ok", "rows", "source", "returned_rows", "truncated"}.issubset(success_payload))
        self.assertFalse(success_payload["truncated"])
        self.assertEqual(len(success_payload["rows"]), 1)
        self.assertEqual(success_payload["rows"][0]["material"], "SiO2")
        self.assertFalse(error_payload["ok"])
        self.assertEqual(set(error_payload), {"ok", "error_type", "error"})
        self.assertEqual(error_payload["error_type"], "ChemXIntegrationError")
        self.assertIn("ChemX SQL query failed", error_payload["error"])
        self.assertIn("SQL columns:", tool_search_chemx_cytotox.__doc__)
        self.assertIn('"viability_%": 113.67', tool_search_chemx_cytotox.__doc__)
        self.assertIn('SELECT material, cell_type, concentration, "viability_%"', tool_search_chemx_cytotox.__doc__)
        self.assertIn("Sample row:", tool_search_chemx_co_crystals.__doc__)
        self.assertIn("SMILES_drug", tool_search_chemx_co_crystals.__doc__)


if __name__ == "__main__":
    unittest.main()
