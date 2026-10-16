"""Tool-friendly wrappers for RDKit-backed molecular structure analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from dataset_store import DatasetStoreError
from dataset_tools import get_dataset_store
from molecular_structure_processing import (
    ConnectedMoleculeDataset,
    MoleculeDataset,
    MoleculeDatasetEntry,
    MolecularStructureError,
    SmilesDatasetSource,
    analyze_smiles,
    connect_smiles_datasets,
    heuristic_adme_metadata,
    profile_molecule_physchem_and_adme_heuristic,
    smiles_to_inchikey,
)
from session_paths import resolve_session_file, session_directory


def _error_payload(error: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "error_type": type(error).__name__,
        "error": str(error),
    }


def _coerce_molecules(molecules: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(molecules, str):
        return [molecules]
    smiles_values = list(molecules)
    if not smiles_values:
        raise ValueError("molecules must contain at least one SMILES string.")
    return smiles_values


def _resolve_source(source: object, session_id: str) -> tuple[SmilesDatasetSource, dict[str, Any]]:
    if isinstance(source, str):
        source = {"file_path": source}
    if not isinstance(source, dict):
        raise ValueError(
            "Each source must be a relative path or a mapping with dataset_id/file_path."
        )
    if "dataset_id" in source:
        if set(source) != {"dataset_id"}:
            raise ValueError(
                "A server source accepts only dataset_id; its path and mapping come from the registry."
            )
        store = get_dataset_store()
        spec = store.registry.resolve(source["dataset_id"])
        if "structure" not in spec.operations:
            raise ValueError(
                f"Dataset {spec.key} does not support structure search; check describe_dataset."
            )
        snapshot = store.prepare(spec.key)
        resolved = SmilesDatasetSource(
            file_path=snapshot.records_path,
            dataset_name=spec.title,
            smiles_field=spec.smiles_field,
            id_field=spec.id_field,
            dataset_id=spec.key,
            version=snapshot.version,
        )
        return resolved, {"dataset_id": spec.key, "title": spec.title, "version": snapshot.version}
    allowed = {"file_path", "dataset_name", "smiles_field", "id_field", "name_field"}
    if "file_path" not in source or set(source) - allowed:
        raise ValueError(
            "A session source needs file_path and optional dataset_name/smiles_field/id_field/name_field."
        )
    for key, value in source.items():
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"Source field {key} must be a nonempty string.")
    path = resolve_session_file(source["file_path"], session_id)
    resolved = SmilesDatasetSource(**{**source, "file_path": str(path)})
    return resolved, {
        "file_path": str(path.relative_to(session_directory(session_id))),
        "dataset_name": resolved.dataset_name or path.stem,
    }


def _read_sources(
    sources: list[str | dict[str, object]],
    session_id: str,
) -> tuple[list[tuple[MoleculeDataset, dict[str, Any]]], list[dict[str, Any]], list[Exception]]:
    if not isinstance(sources, (list, tuple)) or not sources:
        raise ValueError("sources must contain at least one server dataset or session file.")
    session_directory(session_id)
    loaded, reports, errors = [], [], []
    for index, source in enumerate(sources):
        report = {"source_index": index}
        try:
            resolved, identity = _resolve_source(source, session_id)
            report.update(identity)
            dataset = connect_smiles_datasets([resolved], skip_invalid=True).datasets[0]
            report.update(
                status="searched",
                record_count=dataset.record_count,
                valid_records=len(dataset.entries),
                skipped_records=dataset.invalid_record_count,
                skipped_examples=dataset.invalid_examples,
            )
            loaded.append((dataset, report))
        except (
            ValueError,
            TypeError,
            OSError,
            MolecularStructureError,
            DatasetStoreError,
        ) as error:
            errors.append(error)
            report.update(status="error", error_type=type(error).__name__, error=str(error))
        reports.append(report)
    return loaded, reports, errors


def _session_result(entry: MoleculeDatasetEntry, session_id: str) -> dict[str, Any]:
    result = entry.to_dict()
    if entry.source.dataset_id is None:
        result["source"]["file_path"] = str(
            Path(entry.source.file_path).relative_to(session_directory(session_id))
        )
    return result


def tool_profile_molecule_physchem_and_adme_heuristic(
    molecules: list[str],
) -> dict[str, Any]:
    """Calculate physicochemical descriptors and predict ADME properties.

    This deterministic heuristic profile does not require the ADMET-AI model.
    Returns method/descriptor_units/property_metadata/interpretation once per batch,
    including the implemented logS formula, score checks and category thresholds.
    Read these before explaining results: rule scores are not probabilities or
    measured absorption percentages, and the local logS formula is not original ESOL.

    Descriptors: molecular_weight, logp, tpsa, hbd, hba,
    rotatable_bonds, formal_charge, ring_count, aromatic_ring_count,
    heavy_atom_count, fraction_csp3, aromatic_atom_fraction.
    ADME properties: aqueous_solubility_logS,
    human_intestinal_absorption, human_intestinal_absorption_score,
    caco2_permeability, caco2_permeability_score, bbb_permeability,
    bbb_permeability_score, bioavailability_score, lipinski_violations.
    """
    try:
        smiles_values = _coerce_molecules(molecules)
        profiles = profile_molecule_physchem_and_adme_heuristic(smiles_values)
        assert isinstance(profiles, list)
        results = [profile.to_dict() for profile in profiles]
        return {
            "ok": True,
            "results": results,
            **heuristic_adme_metadata(),
        }
    except ValueError as error:
        return _error_payload(error)


def tool_rdkit_smiles_to_inchikey(
    smiles: str,
) -> dict[str, Any]:
    """Resolve a SMILES string to its InChIKey locally via RDKit (no network).

    Use this for an independent, source-agnostic structural check: the InChIKey is
    invariant to the SMILES notation by construction. An unparseable SMILES returns
    is_valid=False (an intrinsic-validity signal) rather than an error.

    Returns: ok, is_valid, inchikey, input_smiles.
    """
    try:
        return smiles_to_inchikey(smiles)
    except ValueError as error:
        return _error_payload(error)


def tool_search_connected_datasets(
    sources: list[str | dict[str, object]],
    *,
    session_id: str,
    query_smiles: str,
    match_type: Literal["exact", "substructure"] = "exact",
    max_results: int = 20,
) -> dict[str, Any]:
    """Search registered server datasets and/or session files using RDKit.

    sources is a list: {"dataset_id":"benzimidazoles"} selects a registered
    dataset (see list_datasets/describe_dataset); {"file_path":"inputs/molecules.csv"}
    or a relative path string selects a session CSV/JSON/JSONL. File mappings may
    add smiles_field, id_field, name_field, dataset_name. Server mappings accept
    only dataset_id: the registry supplies their paths/columns. Sources can be
    combined in one call. Always pass the current session_id.
    exact compares canonical structures, substructure uses RDKit HasSubstructMatch.
    Every selected readable source is searched; max_results limits returned
    matches, not input rows. Results retain dataset version and zero-based row_idx
    for server records, or relative file and row number for session records.
    sources reports matches, failed sources and skipped invalid SMILES; partial
    flags incomplete coverage. All-source failure is an error, not zero matches.
    """
    try:
        if type(max_results) is not int or not 1 <= max_results <= 1000:
            raise ValueError("max_results must be an integer between 1 and 1000.")
        if match_type not in {"exact", "substructure"}:
            raise ValueError("match_type must be either 'exact' or 'substructure'.")
        analyze_smiles(query_smiles)  # Reject bad queries before loading any source.
        loaded, reports, errors = _read_sources(sources, session_id)
        if not loaded:
            return {**_error_payload(errors[0]), "sources": reports, "partial": True}
        matched_rows = 0
        results = []
        for dataset, report in loaded:
            matches = ConnectedMoleculeDataset([dataset]).search(
                query_smiles,
                match_type=match_type,
                max_results=max(len(dataset.entries), 1),
            )
            report["matched_rows"] = len(matches)
            matched_rows += len(matches)
            results.extend(
                _session_result(entry, session_id)
                for entry in matches[: max_results - len(results)]
            )
        return {
            "ok": True,
            "results": results,
            "sources": reports,
            "partial": bool(errors) or any(d.invalid_record_count for d, _ in loaded),
            "matched_rows": matched_rows,
            "returned_rows": len(results),
            "truncated": matched_rows > len(results),
        }
    except (ValueError, TypeError, MolecularStructureError, OSError, DatasetStoreError) as error:
        return _error_payload(error)


def tool_build_structure_research_report(
    sources: list[str | dict[str, object]],
    *,
    session_id: str,
    query_smiles: str,
    criteria: dict[str, dict[str, object]] | None = None,
    hypothesis_name: str | None = None,
    max_exact_matches: int = 5,
    max_substructure_matches: int = 5,
) -> dict[str, Any]:
    """Build a structure-analysis report from the same sources as search_connected_datasets.

    Accepts registered dataset_id mappings and session file paths/mappings. Includes
    source coverage, skipped invalid rows and explicit partial-source failures.
    """
    try:
        analyze_smiles(query_smiles)
        loaded, reports, errors = _read_sources(sources, session_id)
        if not loaded:
            return {**_error_payload(errors[0]), "sources": reports, "partial": True}
        collection = ConnectedMoleculeDataset([dataset for dataset, _ in loaded])
        report = collection.build_research_report(
            query_smiles,
            criteria=criteria,
            hypothesis_name=hypothesis_name,
            max_exact_matches=max_exact_matches,
            max_substructure_matches=max_substructure_matches,
        )
        partial = bool(errors) or any(d.invalid_record_count for d, _ in loaded)
        if partial:
            report["scientific_summary"] = (
                "Search coverage is partial: inspect failed sources and skipped SMILES in sources. "
                + report["scientific_summary"]
            )
        return {"ok": True, "report": report, "sources": reports, "partial": partial}
    except (ValueError, TypeError, MolecularStructureError, OSError, DatasetStoreError) as error:
        return _error_payload(error)


TOOL_REGISTRY = {
    "profile_molecule_physchem_and_adme_heuristic": tool_profile_molecule_physchem_and_adme_heuristic,
    "search_connected_datasets": tool_search_connected_datasets,
    "build_structure_research_report": tool_build_structure_research_report,
    "rdkit_smiles_to_inchikey": tool_rdkit_smiles_to_inchikey,
}


__all__ = [
    "TOOL_REGISTRY",
    "tool_build_structure_research_report",
    "tool_profile_molecule_physchem_and_adme_heuristic",
    "tool_rdkit_smiles_to_inchikey",
    "tool_search_connected_datasets",
]
