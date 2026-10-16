"""Tool-friendly wrappers around PubChem integration for LLM agents."""

from typing import Any, Literal

from pubchem_integration import CompoundRecord, PubChemIntegrationError, PubChemSearchClient


def _build_client(
    *,
    limit: int,
    timeout: float = 20.,
) -> PubChemSearchClient:
    return PubChemSearchClient(
        default_limit=limit,
        timeout=timeout,
    )


def _success_payload(
    *,
    records: list[CompoundRecord],
) -> dict[str, Any]:
    return {
        "ok": True,
        "results": [record.to_dict() for record in records],
    }


def _error_payload(error: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "error_type": type(error).__name__,
        "error": str(error),
    }


def tool_pubchem_search_by_formula(
    formula: str,
    *,
    limit: int = 20,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Search PubChem compounds by molecular formula."""
    try:
        client = _build_client(
            limit=limit,
            timeout=timeout,
        )
        records = client.search_by_formula(formula, limit=limit)
        return _success_payload(records=records)
    except (ValueError, PubChemIntegrationError) as error:
        return _error_payload(error)


def tool_pubchem_search_by_smiles(
    smiles: str,
    *,
    limit: int = 20,
    searchtype: Literal["identity", "substructure", "superstructure", "similarity"] = "identity",
    similarity_threshold: int | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Search PubChem compounds by SMILES string."""
    try:
        client = _build_client(
            limit=limit,
            timeout=timeout,
        )
        options: dict[str, object] = {}
        if searchtype == "similarity" and similarity_threshold is not None:
            options["Threshold"] = similarity_threshold
        records = client.search_by_smiles(
            smiles,
            limit=limit,
            searchtype=searchtype,
            **options,
        )
        return _success_payload(records=records)
    except (ValueError, PubChemIntegrationError) as error:
        return _error_payload(error)


def tool_pubchem_search_by_exact_mass(
    exact_mass: float,
    *,
    tolerance: float = 0.0,
    limit: int = 20,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Search PubChem compounds by exact mass (with optional tolerance)."""
    try:
        client = _build_client(
            limit=limit,
            timeout=timeout,
        )
        records = client.search_by_mass(
            exact_mass=exact_mass,
            tolerance=tolerance,
            limit=limit,
        )
        return _success_payload(records=records)
    except (ValueError, PubChemIntegrationError) as error:
        return _error_payload(error)


def tool_pubchem_search_by_mass_range(
    min_mass: float,
    max_mass: float,
    *,
    limit: int = 20,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Search PubChem compounds by exact-mass range."""
    try:
        client = _build_client(
            limit=limit,
            timeout=timeout,
        )
        records = client.search_by_mass(
            min_mass=min_mass,
            max_mass=max_mass,
            limit=limit,
        )
        return _success_payload(records=records)
    except (ValueError, PubChemIntegrationError) as error:
        return _error_payload(error)


def tool_pubchem_get_by_cids(
    cids: list[int] | list[str] | str,
    *,
    limit: int = 20,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Load PubChem compounds by CID list."""
    cid_values: list[int | str]
    if isinstance(cids, str):
        cid_values = [value.strip() for value in cids.split(",") if value.strip()]
    else:
        cid_values = cids

    try:
        client = _build_client(
            limit=limit,
            timeout=timeout,
        )
        records = client.get_compounds_by_cids(cid_values, limit=limit)
        return _success_payload(records=records)
    except (ValueError, PubChemIntegrationError) as error:
        return _error_payload(error)


def tool_pubchem_search_by_informal_name(
    value: str,
    *,
    output_formats: list[str] | None = None,
) -> dict[str, Any]:
    """Search PubChem compounds by its informal name.

    Args:
        value: Informal name in English language.
        output_formats: Optional. Default:
            ['smiles', 'iupac_name', 'inchi', 'inchikey', 'molecular_formula']
    """
    return tool_pubchem_convert_identifier(value, input_format="name", output_formats=output_formats)


def tool_pubchem_convert_identifier(
    value: str,
    *,
    input_format: Literal[
        "cid",
        "name",
        "formula",
        "inchi",
        "inchikey",
        "smiles",
    ],
    output_formats: list[str] | None = None,
) -> dict[str, Any]:
    """Convert a chemical identifier into alternate representations via PubChem.

    Args:
        value: Source identifier value to resolve in PubChem.
        input_format: Supported values: 'cid', 'name', 'formula', 'inchi', 'inchikey', 'smiles'
        output_formats: Optional. Default:
            ['smiles', 'iupac_name', 'inchi', 'inchikey', 'molecular_formula']
    """
    try:
        client = _build_client(limit=1)
        conversion = client.convert_identifier(
            value,
            input_format=input_format,
            output_formats=output_formats,
        )
        if conversion is None:
            return {
                "ok": True,
                "match_found": False,
                "result": None,
            }
        return {
            "ok": True,
            "match_found": True,
            "result": conversion.to_dict(),
        }
    except (ValueError, PubChemIntegrationError) as error:
        return _error_payload(error)


def tool_pubchem_get_computed_properties(
    value: str,
    *,
    input_format: Literal["cid", "name", "smiles", "inchi", "inchikey", "formula"] = "name",
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Fetch PubChem-computed physicochemical properties for a compound.

    Returns PubChem's own XLogP3, TPSA, H-bond donor/acceptor counts, rotatable
    bonds, molecular weight, exact mass, formal charge, and complexity. Use this to
    verify a descriptor that was sourced from PubChem (e.g. "TPSA is 58.4"): PubChem
    and an RDKit recomputation follow different TPSA/logP conventions, so a
    PubChem-reported value must be checked against PubChem, not against RDKit.

    Args:
        value: Identifier value (an informal name by default).
        input_format: One of 'cid', 'name', 'smiles', 'inchi', 'inchikey', 'formula'.
    """
    try:
        client = _build_client(limit=1, timeout=timeout)
        result = client.get_computed_properties(value, input_format=input_format)
        if result is None:
            return {"ok": True, "match_found": False, "result": None}
        return {"ok": True, "match_found": True, "result": result}
    except (ValueError, PubChemIntegrationError) as error:
        return _error_payload(error)


TOOL_REGISTRY = {
    "pubchem_search_by_formula": tool_pubchem_search_by_formula,
    "pubchem_search_by_smiles": tool_pubchem_search_by_smiles,
    "pubchem_search_by_exact_mass": tool_pubchem_search_by_exact_mass,
    "pubchem_search_by_mass_range": tool_pubchem_search_by_mass_range,
    "pubchem_get_by_cids": tool_pubchem_get_by_cids,
    "pubchem_convert_identifier": tool_pubchem_convert_identifier,
    "pubchem_search_by_informal_name": tool_pubchem_search_by_informal_name,
    "pubchem_get_computed_properties": tool_pubchem_get_computed_properties,
}


__all__ = [
    "tool_pubchem_search_by_formula",
    "tool_pubchem_search_by_smiles",
    "tool_pubchem_search_by_exact_mass",
    "tool_pubchem_search_by_mass_range",
    "tool_pubchem_get_by_cids",
    "tool_pubchem_convert_identifier",
    "tool_pubchem_get_computed_properties",
    "TOOL_REGISTRY",
]
