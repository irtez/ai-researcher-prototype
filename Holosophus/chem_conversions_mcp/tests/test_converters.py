import unittest

from chem_conversions_mcp.converters import convert_chemical_representation


class ConversionTests(unittest.TestCase):
    def test_identity_conversion_is_local_and_normalizes_aliases(self) -> None:
        result = convert_chemical_representation(
            "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
            source_format="inchi_key",  # type: ignore[arg-type]
            target_format="inchikey",
        )

        self.assertEqual(
            result,
            {
                "input": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                "source_format": "inchikey",
                "target_format": "inchikey",
                "result": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
            },
        )


if __name__ == "__main__":
    unittest.main()
