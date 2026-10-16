import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from pubchem_integration import CompoundRecord, PubChemIntegrationError, PubChemSearchClient


def _compound(**overrides: object) -> SimpleNamespace:
    payload = {
        "cid": 2244,
        "molecular_formula": "C9H8O4",
        "molecular_weight": 180.16,
        "exact_mass": 180.042259,
        "smiles": "CC(=O)Oc1ccccc1C(=O)O",
        "connectivity_smiles": None,
        "iupac_name": "2-acetyloxybenzoic acid",
        "inchi": "InChI=1S/C9H8O4/c1-6(10)13-8-5-3-2-4-7(8)9(11)12/h2-5H,1H3,(H,11,12)",
        "inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


class PubChemIntegrationTests(unittest.TestCase):
    def test_search_by_formula_returns_normalized_records(self) -> None:
        client = PubChemSearchClient(cache_mode="off")
        compounds = [
            _compound(),
            _compound(
                cid="3672",
                molecular_formula="C8H9NO2",
                molecular_weight="151.16",
                exact_mass="151.063329",
                smiles=None,
                connectivity_smiles="CC(=O)NC1=CC=C(O)C=C1",
                iupac_name="N-(4-hydroxyphenyl)acetamide",
                inchi="InChI=1S/C8H9NO2/c1-6(10)9-7-2-4-8(11)5-3-7/h2-5,11H,1H3,(H,9,10)",
                inchikey="RZVAJINKPMORJF-UHFFFAOYSA-N",
            ),
        ]

        with patch("pubchem_integration.pcp.get_compounds", return_value=compounds) as get_compounds:
            records = client.search_by_formula(" C9H8O4 ", limit=1)

        get_compounds.assert_called_once_with(
            "C9H8O4",
            namespace="formula",
            listkey_count=1,
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].cid, 2244)
        self.assertEqual(records[0].molecular_formula, "C9H8O4")
        self.assertEqual(records[0].pubchem_url, "https://pubchem.ncbi.nlm.nih.gov/compound/2244")

    def test_search_by_smiles_returns_records_for_smiles_queries(self) -> None:
        client = PubChemSearchClient(cache_mode="off")
        water = _compound(
            cid=962,
            molecular_formula="H2O",
            molecular_weight=18.015,
            exact_mass=18.010565,
            smiles=None,
            connectivity_smiles="O",
            iupac_name="oxidane",
            inchi="InChI=1S/H2O/h1H2",
            inchikey="XLYOFNOQVPJJNP-UHFFFAOYSA-N",
        )

        with patch("pubchem_integration.pcp.get_compounds", return_value=[water]) as get_compounds:
            records = client.search_by_smiles(" O ", limit=2)

        get_compounds.assert_called_once_with(
            "O",
            namespace="smiles",
            searchtype="identity",
            listkey_count=2,
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].cid, 962)
        self.assertEqual(records[0].smiles, "O")
        self.assertEqual(records[0].inchikey, "XLYOFNOQVPJJNP-UHFFFAOYSA-N")

    def test_search_by_mass_supports_exact_value_and_range_queries(self) -> None:
        client = PubChemSearchClient(cache_mode="off")
        water = _compound(
            cid=962,
            molecular_formula="H2O",
            molecular_weight=18.015,
            exact_mass=18.010565,
            smiles="O",
            iupac_name="oxidane",
            inchi="InChI=1S/H2O/h1H2",
            inchikey="XLYOFNOQVPJJNP-UHFFFAOYSA-N",
        )

        with (
            patch.object(
                client,
                "_fetch_json",
                side_effect=[
                    {"esearchresult": {"idlist": ["2244", "3672"]}},
                    {"esearchresult": {"idlist": ["962"]}},
                ],
            ) as fetch_json,
            patch(
                "pubchem_integration.pcp.get_compounds",
                side_effect=[
                    [_compound(), _compound(cid=3672, molecular_formula="C8H9NO2")],
                    [water],
                ],
            ) as get_compounds,
        ):
            exact_records = client.search_by_mass(exact_mass=180.042259, tolerance=0.001, limit=2)
            range_records = client.search_by_mass(min_mass=18.0, max_mass=18.02, limit=1)

        self.assertEqual(
            fetch_json.call_args_list[0].kwargs["params"]["term"],
            "180.041259:180.043259[exactmass]",
        )
        self.assertEqual(
            fetch_json.call_args_list[1].kwargs["params"]["term"],
            "18.000000:18.020000[exactmass]",
        )
        self.assertEqual(get_compounds.call_args_list[0].args[0], [2244, 3672])
        self.assertEqual(get_compounds.call_args_list[0].kwargs["namespace"], "cid")
        self.assertEqual(get_compounds.call_args_list[1].args[0], [962])
        self.assertEqual(exact_records[0].cid, 2244)
        self.assertEqual(range_records[0].cid, 962)

    def test_convert_identifier_supports_aliases_and_requested_outputs(self) -> None:
        client = PubChemSearchClient(cache_mode="off")
        record = CompoundRecord(
            cid=2244,
            molecular_formula="C9H8O4",
            molecular_weight=180.16,
            exact_mass=180.042259,
            smiles="CC(=O)Oc1ccccc1C(=O)O",
            iupac_name="2-acetyloxybenzoic acid",
            inchi="InChI=1S/C9H8O4/c1-6(10)13-8-5-3-2-4-7(8)9(11)12/h2-5H,1H3,(H,11,12)",
            inchikey="BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
            pubchem_url="https://pubchem.ncbi.nlm.nih.gov/compound/2244",
        )

        with patch.object(client, "search_by_identifier", return_value=[record]) as search:
            conversion = client.convert_identifier(
                "aspirin",
                input_format="common_name",
                output_formats=("smiles", "inchi_key", "formula"),
            )

        search.assert_called_once_with("aspirin", namespace="name", limit=1)
        self.assertIsNotNone(conversion)
        assert conversion is not None
        self.assertEqual(conversion.input_format, "name")
        self.assertEqual(conversion.outputs["smiles"], "CC(=O)Oc1ccccc1C(=O)O")
        self.assertEqual(conversion.outputs["inchi_key"], "BSYNRYMUTXBXSQ-UHFFFAOYSA-N")
        self.assertEqual(conversion.outputs["formula"], "C9H8O4")

    def test_convert_identifier_supports_inchikey_inputs(self) -> None:
        client = PubChemSearchClient(cache_mode="off")
        record = CompoundRecord(
            cid=2244,
            molecular_formula="C9H8O4",
            molecular_weight=180.16,
            exact_mass=180.042259,
            smiles="CC(=O)Oc1ccccc1C(=O)O",
            iupac_name="2-acetyloxybenzoic acid",
            inchi="InChI=1S/C9H8O4/c1-6(10)13-8-5-3-2-4-7(8)9(11)12/h2-5H,1H3,(H,11,12)",
            inchikey="BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
            pubchem_url="https://pubchem.ncbi.nlm.nih.gov/compound/2244",
        )

        with patch.object(client, "search_by_identifier", return_value=[record]) as search:
            conversion = client.convert_identifier(
                "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                input_format="inchi_key",
                output_formats=("smiles", "iupac_name"),
            )

        search.assert_called_once_with("BSYNRYMUTXBXSQ-UHFFFAOYSA-N", namespace="inchikey", limit=1)
        self.assertIsNotNone(conversion)
        assert conversion is not None
        self.assertEqual(conversion.input_format, "inchikey")
        self.assertEqual(conversion.outputs["smiles"], "CC(=O)Oc1ccccc1C(=O)O")
        self.assertEqual(conversion.outputs["iupac_name"], "2-acetyloxybenzoic acid")

    def test_convert_identifier_supports_reverse_conversion_from_smiles(self) -> None:
        client = PubChemSearchClient(cache_mode="off")
        record = CompoundRecord(
            cid=2244,
            molecular_formula="C9H8O4",
            molecular_weight=180.16,
            exact_mass=180.042259,
            smiles="CC(=O)Oc1ccccc1C(=O)O",
            iupac_name="2-acetyloxybenzoic acid",
            inchi="InChI=1S/C9H8O4/c1-6(10)13-8-5-3-2-4-7(8)9(11)12/h2-5H,1H3,(H,11,12)",
            inchikey="BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
            pubchem_url="https://pubchem.ncbi.nlm.nih.gov/compound/2244",
        )

        with patch.object(client, "search_by_identifier", return_value=[record]) as search:
            conversion = client.convert_identifier(
                "CC(=O)Oc1ccccc1C(=O)O",
                input_format="smiles",
                output_formats=("iupac_name", "inchi", "formula"),
            )

        search.assert_called_once_with("CC(=O)Oc1ccccc1C(=O)O", namespace="smiles", limit=1)
        self.assertIsNotNone(conversion)
        assert conversion is not None
        self.assertEqual(conversion.input_format, "smiles")
        self.assertEqual(conversion.outputs["iupac_name"], "2-acetyloxybenzoic acid")
        self.assertEqual(
            conversion.outputs["inchi"],
            "InChI=1S/C9H8O4/c1-6(10)13-8-5-3-2-4-7(8)9(11)12/h2-5H,1H3,(H,11,12)",
        )
        self.assertEqual(conversion.outputs["formula"], "C9H8O4")

    def test_search_by_formula_uses_sqlite_cache(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "pubchem.sqlite"
            client = PubChemSearchClient(cache_path=cache_path)

            with patch("pubchem_integration.pcp.get_compounds", return_value=[_compound()]) as get_compounds:
                first_records = client.search_by_formula("C9H8O4", limit=1)

            cached_client = PubChemSearchClient(cache_path=cache_path)
            with patch("pubchem_integration.pcp.get_compounds") as get_compounds:
                second_records = cached_client.search_by_formula("C9H8O4", limit=1)

            self.assertEqual(first_records, second_records)
            get_compounds.assert_not_called()

    def test_read_only_cache_mode_does_not_call_pubchem_on_miss(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "pubchem.sqlite"
            client = PubChemSearchClient(cache_path=cache_path, cache_mode="read_only")

            with patch("pubchem_integration.pcp.get_compounds") as get_compounds:
                with self.assertRaises(PubChemIntegrationError):
                    client.search_by_formula("C9H8O4", limit=1)

            get_compounds.assert_not_called()


if __name__ == "__main__":
    unittest.main()
