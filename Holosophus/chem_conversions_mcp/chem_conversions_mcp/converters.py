from __future__ import annotations

from typing import Literal
from urllib.parse import quote

import requests


SourceFormat = Literal["smiles", "inchi", "inchikey", "iupac", "name"]
TargetFormat = Literal["smiles", "inchi", "inchikey", "iupac", "name"]

PUBCHEM_BASE_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound"
PUBCHEM_PROPERTIES = (
    "SMILES",
    "ConnectivitySMILES",
    "CanonicalSMILES",
    "IsomericSMILES",
    "InChI",
    "InChIKey",
    "IUPACName",
    "Title",
)
_FORMAT_ALIASES = {
    "inchi_key": "inchikey",
    "iupac_name": "iupac",
    "common_name": "name",
    "trivial_name": "name",
}


def _normalize_format(raw: str) -> str:
    lowered = raw.strip().lower()
    return _FORMAT_ALIASES.get(lowered, lowered)


def _request_json(url: str) -> dict:
    response = requests.get(url, timeout=15)
    if response.status_code == 404:
        raise ValueError("No matching compound found in PubChem for this identifier.")
    response.raise_for_status()
    return response.json()


def _request_json_post(url: str, form_data: dict[str, str]) -> dict:
    response = requests.post(url, data=form_data, timeout=15)
    if response.status_code == 404:
        raise ValueError("No matching compound found in PubChem for this identifier.")
    response.raise_for_status()
    return response.json()


def _source_namespace(source_format: SourceFormat) -> str:
    if source_format in {"name", "iupac"}:
        return "name"
    return source_format


def _fetch_candidate_cids(value: str, source_format: SourceFormat) -> list[int]:
    namespace = _source_namespace(source_format)
    encoded_value = quote(value, safe="")
    url = f"{PUBCHEM_BASE_URL}/{namespace}/{encoded_value}/cids/JSON"
    try:
        payload = _request_json(url)
    except requests.HTTPError as exc:
        # InChI strings frequently require POST form payloads for PubChem.
        status_code = exc.response.status_code if exc.response is not None else None
        if namespace == "inchi" and status_code == 400:
            post_url = f"{PUBCHEM_BASE_URL}/inchi/cids/JSON"
            payload = _request_json_post(post_url, {"inchi": value})
        else:
            raise
    cids = payload.get("IdentifierList", {}).get("CID", [])
    return [cid for cid in cids if isinstance(cid, int)]


def _fetch_pubchem_properties_by_cid(cid: int) -> dict[str, str]:
    properties_arg = ",".join(PUBCHEM_PROPERTIES)
    url = f"{PUBCHEM_BASE_URL}/cid/{cid}/property/{properties_arg}/JSON"
    payload = _request_json(url)
    props = payload.get("PropertyTable", {}).get("Properties", [])
    if not props:
        raise ValueError("PubChem returned no properties for this CID.")
    return props[0]


def _pick_target_value(
    props: dict[str, str],
    target_format: TargetFormat,
    prefer_isomeric_smiles: bool,
) -> str:
    if target_format == "smiles":
        if props.get("SMILES"):
            return props["SMILES"]
        if props.get("ConnectivitySMILES"):
            return props["ConnectivitySMILES"]
        if prefer_isomeric_smiles and props.get("IsomericSMILES"):
            return props["IsomericSMILES"]
        if props.get("CanonicalSMILES"):
            return props["CanonicalSMILES"]
        if props.get("IsomericSMILES"):
            return props["IsomericSMILES"]
        raise ValueError("SMILES value is unavailable for this compound.")

    if target_format == "inchi":
        value = props.get("InChI")
        if value:
            return value
        raise ValueError("InChI value is unavailable for this compound.")

    if target_format == "inchikey":
        value = props.get("InChIKey")
        if value:
            return value
        raise ValueError("InChIKey value is unavailable for this compound.")

    if target_format == "iupac":
        value = props.get("IUPACName")
        if value:
            return value
        raise ValueError("IUPAC name is unavailable for this compound.")

    if target_format == "name":
        value = props.get("Title") or props.get("IUPACName")
        if value:
            return value
        raise ValueError("Common name is unavailable for this compound.")

    raise ValueError(f"Unsupported target format: {target_format}")


def convert_chemical_representation(
    value: str,
    source_format: SourceFormat,
    target_format: TargetFormat,
    prefer_isomeric_smiles: bool = True,
) -> dict[str, str]:
    normalized_source = _normalize_format(source_format)  # type: ignore[arg-type]
    normalized_target = _normalize_format(target_format)  # type: ignore[arg-type]
    if normalized_source not in {"smiles", "inchi", "inchikey", "iupac", "name"}:
        raise ValueError(f"Unsupported source format: {source_format}")
    if normalized_target not in {"smiles", "inchi", "inchikey", "iupac", "name"}:
        raise ValueError(f"Unsupported target format: {target_format}")

    if normalized_source == normalized_target:
        return {
            "input": value,
            "source_format": normalized_source,
            "target_format": normalized_target,
            "result": value,
        }

    cids = _fetch_candidate_cids(value, normalized_source)  # type: ignore[arg-type]
    if not cids:
        raise ValueError("No candidate compounds found in PubChem for this identifier.")

    result_value = ""
    for cid in cids[:10]:
        try:
            properties = _fetch_pubchem_properties_by_cid(cid)
            result_value = _pick_target_value(
                properties,
                normalized_target,  # type: ignore[arg-type]
                prefer_isomeric_smiles,
            )
            break
        except ValueError:
            continue

    if not result_value:
        raise ValueError(
            "Target representation is unavailable for candidate compounds in PubChem."
        )

    return {
        "input": value,
        "source_format": normalized_source,
        "target_format": normalized_target,
        "result": result_value,
    }
