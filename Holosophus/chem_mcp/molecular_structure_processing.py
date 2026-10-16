"""RDKit-backed utilities for SMILES validation, dataset search, and research enrichment."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Literal, Sequence

from rdkit import Chem, RDLogger, rdBase
from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors, rdmolops

RDLogger.DisableLog("rdApp.error")

SUPPORTED_DATASET_EXTENSIONS = {".csv", ".json", ".jsonl"}
SMILES_FIELD_HINTS = ("smiles", "isomeric_smiles", "structure_smiles", "connectivity_smiles")
NAME_FIELD_HINTS = ("name", "compound_name", "compound", "molecule_name", "chemical_name")
ID_FIELD_HINTS = ("id", "compound_id", "molecule_id", "cid")
SCIENTIFIC_TEXT_SECTIONS = ("article", "report", "hypothesis")


class MolecularStructureError(RuntimeError):
    """Raised when molecular structure processing cannot satisfy the request."""


@dataclass(frozen=True)
class SmilesDatasetSource:
    """Configuration for a single external dataset with a SMILES column."""

    file_path: str | Path
    dataset_name: str | None = None
    smiles_field: str | None = None
    id_field: str | None = None
    name_field: str | None = None
    dataset_id: str | None = None
    version: str | None = None

    @classmethod
    def coerce(
        cls, value: "SmilesDatasetSource | dict[str, object] | str | Path"
    ) -> "SmilesDatasetSource":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(**value)
        return cls(file_path=value)

    def resolved_path(self) -> Path:
        return Path(self.file_path).expanduser().resolve()


@dataclass(frozen=True)
class DatasetReference:
    """Source trace for a molecule loaded from an external dataset."""

    dataset_name: str
    file_path: str
    row_number: int
    record_id: str | None = None
    dataset_id: str | None = None
    version: str | None = None
    row_idx: int | None = None

    def label(self) -> str:
        location = f"{Path(self.file_path).name}::{self.dataset_name}::row {self.row_number}"
        if self.record_id:
            return f"{location}::{self.record_id}"
        return location

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        if self.dataset_id is not None:
            payload.pop(
                "file_path"
            )  # Server snapshots are selected by ID, not agent-visible paths.
        else:
            for key in ("dataset_id", "version", "row_idx"):
                payload.pop(key)
        payload["label"] = self.label()
        return payload


@dataclass(frozen=True)
class MoleculeAnalysis:
    """Validated and normalized structure properties for one SMILES string."""

    input_smiles: str
    connectivity_smiles: str | None
    is_valid: bool
    molecular_formula: str | None
    molecular_weight: float | None
    exact_mass: float | None
    descriptors: dict[str, float | int]
    adme_predictions: dict[str, float | int | str]
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "input_smiles": self.input_smiles,
            "connectivity_smiles": self.connectivity_smiles,
            "is_valid": self.is_valid,
            "molecular_formula": self.molecular_formula,
            "molecular_weight": self.molecular_weight,
            "exact_mass": self.exact_mass,
            "descriptors": dict(self.descriptors),
            "adme_predictions": dict(self.adme_predictions),
            "errors": list(self.errors),
        }

    def metric_values(self) -> dict[str, float | int | str | None]:
        return {
            "connectivity_smiles": self.connectivity_smiles,
            "molecular_formula": self.molecular_formula,
            "molecular_weight": self.molecular_weight,
            "exact_mass": self.exact_mass,
            **self.descriptors,
            **self.adme_predictions,
        }


@dataclass
class MoleculeDatasetEntry:
    """One row from a connected external dataset."""

    source: DatasetReference
    raw_smiles: str
    connectivity_smiles: str | None
    compound_name: str | None
    metadata: dict[str, object]
    analysis: MoleculeAnalysis
    _mol: Chem.Mol | None = field(default=None, repr=False, compare=False)

    @property
    def is_valid(self) -> bool:
        return self.analysis.is_valid and self._mol is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source.to_dict(),
            "raw_smiles": self.raw_smiles,
            "connectivity_smiles": self.connectivity_smiles,
            "compound_name": self.compound_name,
            "metadata": dict(self.metadata),
            "analysis": self.analysis.to_dict(),
        }


@dataclass
class MoleculeDataset:
    """Loaded dataset with per-row structure analysis."""

    name: str
    path: str
    entries: list[MoleculeDatasetEntry]
    invalid_record_count: int = 0
    invalid_examples: list[dict[str, object]] = field(default_factory=list)

    @property
    def record_count(self) -> int:
        return len(self.entries) + self.invalid_record_count


@dataclass
class ConnectedMoleculeDataset:
    """Collection of connected external datasets with deterministic search."""

    datasets: list[MoleculeDataset]

    @classmethod
    def from_sources(
        cls,
        sources: Sequence[SmilesDatasetSource | dict[str, object] | str | Path],
        *,
        skip_invalid: bool = False,
    ) -> "ConnectedMoleculeDataset":
        if not sources:
            raise ValueError("At least one dataset source is required.")
        datasets = [
            _load_dataset(SmilesDatasetSource.coerce(source), skip_invalid=skip_invalid)
            for source in sources
        ]
        return cls(datasets=datasets)

    @property
    def entries(self) -> list[MoleculeDatasetEntry]:
        items: list[MoleculeDatasetEntry] = []
        for dataset in self.datasets:
            items.extend(dataset.entries)
        return items

    def summary(self) -> dict[str, object]:
        entries = self.entries
        return {
            "dataset_count": len(self.datasets),
            "record_count": sum(dataset.record_count for dataset in self.datasets),
            "valid_record_count": sum(entry.is_valid for entry in entries),
            "invalid_record_count": sum(dataset.invalid_record_count for dataset in self.datasets)
            + sum(not entry.is_valid for entry in entries),
            "datasets": [
                {
                    "name": dataset.name,
                    "path": dataset.path,
                    "record_count": dataset.record_count,
                }
                for dataset in self.datasets
            ],
        }

    def search(
        self,
        query_smiles: str,
        *,
        match_type: Literal["exact", "substructure"] = "exact",
        max_results: int = 20,
    ) -> list[MoleculeDatasetEntry]:
        if match_type not in {"exact", "substructure"}:
            raise ValueError("match_type must be either 'exact' or 'substructure'.")
        if max_results <= 0:
            raise ValueError("max_results must be a positive integer.")
        query_analysis, query_mol = _require_valid_smiles(query_smiles)
        matches: list[MoleculeDatasetEntry] = []
        for entry in self.entries:
            if not entry.is_valid:
                continue
            if match_type == "exact":
                is_match = entry.connectivity_smiles == query_analysis.connectivity_smiles
            else:
                assert query_mol is not None
                assert entry._mol is not None
                is_match = entry._mol.HasSubstructMatch(query_mol)
            if not is_match:
                continue
            matches.append(entry)
            if len(matches) >= max_results:
                break
        return matches

    def build_research_report(
        self,
        query_smiles: str,
        *,
        criteria: dict[str, dict[str, object]] | None = None,
        hypothesis_name: str | None = None,
        max_exact_matches: int = 5,
        max_substructure_matches: int = 5,
    ) -> dict[str, object]:
        analysis, _ = _require_valid_smiles(query_smiles)
        exact_matches = self.search(query_smiles, match_type="exact", max_results=max_exact_matches)
        substructure_matches = self.search(
            query_smiles,
            match_type="substructure",
            max_results=max_substructure_matches,
        )
        hypothesis_assessment = _evaluate_criteria(
            analysis, criteria=criteria, hypothesis_name=hypothesis_name
        )
        return {
            "query": analysis.to_dict(),
            "dataset_summary": self.summary(),
            "exact_matches": [entry.to_dict() for entry in exact_matches],
            "substructure_matches": [entry.to_dict() for entry in substructure_matches],
            "hypothesis_assessment": hypothesis_assessment,
            "scientific_summary": self.build_scientific_summary(
                query_smiles,
                criteria=criteria,
                hypothesis_name=hypothesis_name,
                max_exact_matches=max_exact_matches,
                max_substructure_matches=max_substructure_matches,
            ),
        }

    def build_scientific_summary(
        self,
        query_smiles: str,
        *,
        criteria: dict[str, dict[str, object]] | None = None,
        hypothesis_name: str | None = None,
        section: Literal["article", "report", "hypothesis"] = "report",
        max_exact_matches: int = 3,
        max_substructure_matches: int = 3,
    ) -> str:
        if section not in SCIENTIFIC_TEXT_SECTIONS:
            allowed = ", ".join(SCIENTIFIC_TEXT_SECTIONS)
            raise ValueError(f"Unsupported section '{section}'. Allowed values: {allowed}.")
        analysis, _ = _require_valid_smiles(query_smiles)
        exact_matches = self.search(query_smiles, match_type="exact", max_results=max_exact_matches)
        substructure_matches = self.search(
            query_smiles,
            match_type="substructure",
            max_results=max_substructure_matches,
        )
        assessment = _evaluate_criteria(
            analysis, criteria=criteria, hypothesis_name=hypothesis_name
        )

        descriptors = analysis.descriptors
        adme = analysis.adme_predictions
        fragments = [
            (
                f"Validated SMILES {analysis.connectivity_smiles} corresponds to formula {analysis.molecular_formula} "
                f"with molecular weight {analysis.molecular_weight:.3f} and exact mass {analysis.exact_mass:.6f}."
            ),
            (
                f"Descriptor profile: cLogP {descriptors['logp']:.3f}, TPSA {descriptors['tpsa']:.2f}, "
                f"HBD {descriptors['hbd']}, HBA {descriptors['hba']}, rotatable bonds {descriptors['rotatable_bonds']}, "
                f"ring count {descriptors['ring_count']}."
            ),
            (
                f"Heuristic ADME profile indicates logS {adme['aqueous_solubility_logS']:.3f}, "
                f"HIA {adme['human_intestinal_absorption']}, BBB permeability {adme['bbb_permeability']}, "
                f"Caco-2 permeability {adme['caco2_permeability']}, and bioavailability score {adme['bioavailability_score']:.2f}."
            ),
        ]

        if exact_matches:
            labels = _format_entry_labels(exact_matches)
            fragments.append(f"Exact structure matches were found in connected datasets: {labels}.")
        elif substructure_matches:
            labels = _format_entry_labels(substructure_matches)
            fragments.append(f"Substructure evidence was found in connected datasets: {labels}.")
        else:
            fragments.append(
                "No exact or substructure evidence was found in the connected external datasets."
            )

        if assessment is not None:
            satisfied = assessment["satisfied_count"]
            total = assessment["criteria_count"]
            fragments.append(
                f"Hypothesis checks for {assessment['hypothesis_name']} satisfied {satisfied}/{total} deterministic criteria."
            )
            if assessment["failed_checks"]:
                failed = ", ".join(check["metric"] for check in assessment["failed_checks"])
                fragments.append(f"The criteria that remain unsupported are: {failed}.")

        if section == "article":
            fragments.append(
                "These grounded structure-level results can be cited directly in a manuscript discussion."
            )
        elif section == "hypothesis":
            fragments.append(
                "The same evidence can be reused by an agent-researcher to accept or reject lead-selection hypotheses."
            )
        else:
            fragments.append(
                "The summary is suitable for report enrichment with explicit source grounding."
            )
        return " ".join(fragments)


def analyze_smiles(smiles: str) -> MoleculeAnalysis:
    """Validate SMILES and calculate base properties, descriptors, and ADME heuristics."""
    if not isinstance(smiles, str) or not smiles.strip():
        raise ValueError("smiles must be a non-empty string.")
    input_smiles = smiles.strip()
    mol = Chem.MolFromSmiles(input_smiles)
    if mol is None:
        raise ValueError(f"{smiles} is invalid SMILES structure.")

    canonical = Chem.MolToSmiles(mol, canonical=True)
    formula = rdMolDescriptors.CalcMolFormula(mol)
    molecular_weight = round(float(Descriptors.MolWt(mol)), 6)
    exact_mass = round(float(Descriptors.ExactMolWt(mol)), 6)

    descriptors = _calculate_descriptors(mol)
    adme_predictions = _predict_adme(descriptors)
    return MoleculeAnalysis(
        input_smiles=input_smiles,
        connectivity_smiles=canonical,
        is_valid=True,
        molecular_formula=formula,
        molecular_weight=molecular_weight,
        exact_mass=exact_mass,
        descriptors=descriptors,
        adme_predictions=adme_predictions,
        errors=(),
    )


def smiles_to_inchikey(smiles: str) -> dict[str, object]:
    """Resolve a SMILES string to its InChIKey via RDKit (no network).

    A thin, source-independent structural resolver: it parses the SMILES locally
    and derives the InChIKey with ``Chem.MolToInchiKey``. This is the SMILES leg of
    the representation-consistency check — independent of PubChem, so comparing it
    against a name/InChI resolved through PubChem is a real cross-engine structural
    check rather than asking one source twice.

    Returns ``{"ok", "is_valid", "inchikey", "input_smiles", "error"?}``. A SMILES
    that RDKit cannot parse yields ``is_valid=False`` (an intrinsic-validity signal),
    not an exception.
    """
    if not isinstance(smiles, str) or not smiles.strip():
        return {
            "ok": False,
            "is_valid": False,
            "inchikey": None,
            "input_smiles": smiles,
            "error": "smiles must be a non-empty string.",
        }
    input_smiles = smiles.strip()
    mol = Chem.MolFromSmiles(input_smiles)
    if mol is None:
        return {
            "ok": True,
            "is_valid": False,
            "inchikey": None,
            "input_smiles": input_smiles,
        }
    try:
        inchikey = Chem.MolToInchiKey(mol)
    except Exception as error:  # InChI generation can fail on exotic structures
        return {
            "ok": False,
            "is_valid": True,
            "inchikey": None,
            "input_smiles": input_smiles,
            "error": str(error),
        }
    return {
        "ok": True,
        "is_valid": True,
        "inchikey": inchikey or None,
        "input_smiles": input_smiles,
    }


def profile_molecule_physchem_and_adme_heuristic(
    molecules: str | Sequence[str],
) -> MoleculeAnalysis | list[MoleculeAnalysis]:
    """Profile one molecule or a batch with descriptors and ADME heuristics."""
    if isinstance(molecules, str):
        return analyze_smiles(molecules)
    if not molecules:
        raise ValueError("molecules must contain at least one SMILES string.")
    return [analyze_smiles(smiles) for smiles in molecules]


def connect_smiles_datasets(
    sources: Sequence[SmilesDatasetSource | dict[str, object] | str | Path],
    *,
    skip_invalid: bool = False,
) -> ConnectedMoleculeDataset:
    """Convenience wrapper for loading multiple external datasets with SMILES columns."""
    return ConnectedMoleculeDataset.from_sources(sources, skip_invalid=skip_invalid)


def _load_dataset(source: SmilesDatasetSource, *, skip_invalid: bool = False) -> MoleculeDataset:
    path = source.resolved_path()
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_DATASET_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_DATASET_EXTENSIONS))
        raise ValueError(
            f"Unsupported dataset extension '{path.suffix}'. Allowed values: {allowed}."
        )
    dataset_name = source.dataset_name or path.stem
    rows = _load_rows(path)
    entries: list[MoleculeDatasetEntry] = []
    if not rows and suffix in {".json", ".jsonl"} and not source.dataset_id:
        # Preserve empty file datasets: without rows there is no schema to infer.
        return MoleculeDataset(name=dataset_name, path=str(path), entries=[])
    # Validate mappings against the source schema, rather than treating a typo as
    # a dataset full of invalid molecules. Missing values in individual rows can be skipped.
    columns = {key: None for _, row in rows for key in row}
    if not rows and suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            columns = {key: None for key in (csv.DictReader(handle).fieldnames or [])}
    if not rows and source.smiles_field and source.dataset_id:
        columns = {
            key: None for key in (source.smiles_field, source.id_field, source.name_field) if key
        }
    smiles_field = _resolve_field_name(columns, source.smiles_field, SMILES_FIELD_HINTS, "smiles")
    if smiles_field is None:
        raise MolecularStructureError(
            f"Dataset '{path.name}' is missing a SMILES field. Expected one of: {', '.join(SMILES_FIELD_HINTS)}."
        )
    id_field = _resolve_field_name(columns, source.id_field, ID_FIELD_HINTS, "id", required=False)
    name_field = _resolve_field_name(
        columns, source.name_field, NAME_FIELD_HINTS, "name", required=False
    )
    invalid_count = 0
    invalid_examples = []
    for row_idx, (row_number, row) in enumerate(rows):
        raw_smiles = _clean_text(row.get(smiles_field))
        record_id = _clean_text(row.get(id_field)) if id_field else None
        compound_name = _clean_text(row.get(name_field)) if name_field else None
        try:
            if raw_smiles is None:
                raise ValueError("empty SMILES value")
            analysis = analyze_smiles(raw_smiles)
        except ValueError as error:
            message = f"Dataset '{path.name}' has invalid SMILES '{raw_smiles}' at row {row_number}: {error}"
            if not skip_invalid:
                raise MolecularStructureError(message) from error
            invalid_count += 1
            if len(invalid_examples) < 5:
                invalid_examples.append(
                    {
                        "row_number": row_number,
                        "record_id": record_id,
                        "raw_smiles": raw_smiles,
                        "error": message,
                    }
                )
            continue
        mol = (
            Chem.MolFromSmiles(analysis.connectivity_smiles)
            if analysis.connectivity_smiles
            else None
        )

        metadata = {
            key: value
            for key, value in row.items()
            if key not in {smiles_field, id_field, name_field}
        }
        entries.append(
            MoleculeDatasetEntry(
                source=DatasetReference(
                    dataset_name=dataset_name,
                    file_path=str(path),
                    row_number=row_number,
                    record_id=record_id,
                    dataset_id=source.dataset_id,
                    version=source.version,
                    row_idx=row_idx if source.dataset_id else None,
                ),
                raw_smiles=raw_smiles,
                connectivity_smiles=analysis.connectivity_smiles,
                compound_name=compound_name,
                metadata=metadata,
                analysis=analysis,
                _mol=mol,
            )
        )
    return MoleculeDataset(
        name=dataset_name,
        path=str(path),
        entries=entries,
        invalid_record_count=invalid_count,
        invalid_examples=invalid_examples,
    )


def _load_rows(path: Path) -> list[tuple[int, dict[str, object]]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = []
            for index, row in enumerate(reader, start=2):
                if None in row:
                    raise MolecularStructureError(
                        f"Dataset '{path.name}' has more values than column headers at row {index}."
                    )
                if row:
                    rows.append((index, dict(row)))
            return rows
    if suffix == ".jsonl":
        rows: list[tuple[int, dict[str, object]]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise MolecularStructureError(
                        f"Expected JSON object on line {line_number} of '{path.name}'."
                    )
                rows.append((line_number, payload))
        return rows

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("records"), list):
        items = payload["records"]
    else:
        raise MolecularStructureError(f"JSON dataset '{path.name}' must contain a list of records.")

    rows: list[tuple[int, dict[str, object]]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise MolecularStructureError(f"Record {index} in '{path.name}' must be a JSON object.")
        rows.append((index, item))
    return rows


def _calculate_descriptors(mol: Chem.Mol) -> dict[str, float | int]:
    heavy_atom_count = int(Descriptors.HeavyAtomCount(mol))
    aromatic_atom_count = sum(atom.GetIsAromatic() for atom in mol.GetAtoms())
    return {
        "molecular_weight": round(float(Descriptors.MolWt(mol)), 6),
        "logp": round(float(Crippen.MolLogP(mol)), 6),
        "tpsa": round(float(rdMolDescriptors.CalcTPSA(mol)), 6),
        "hbd": int(Lipinski.NumHDonors(mol)),
        "hba": int(Lipinski.NumHAcceptors(mol)),
        "rotatable_bonds": int(Lipinski.NumRotatableBonds(mol)),
        "formal_charge": int(rdmolops.GetFormalCharge(mol)),
        "ring_count": int(rdMolDescriptors.CalcNumRings(mol)),
        "aromatic_ring_count": int(rdMolDescriptors.CalcNumAromaticRings(mol)),
        "heavy_atom_count": heavy_atom_count,
        "fraction_csp3": round(float(Lipinski.FractionCSP3(mol)), 6),
        "aromatic_atom_fraction": round(
            float(aromatic_atom_count / heavy_atom_count) if heavy_atom_count else 0.0,
            6,
        ),
    }


def heuristic_adme_metadata() -> dict[str, object]:
    """Describe the local rules, once per tool response, without loading a model.

    This documents the existing implementation, including score rounding before
    classification. It does not substitute published ESOL or clinical models.
    """
    properties: dict[str, dict[str, object]] = {
        "aqueous_solubility_logS": {
            "name": "Aqueous solubility heuristic",
            "units": "log10(mol/L)",
            "formula": "0.16 - logp - 0.0062 * molecular_weight + 0.066 * rotatable_bonds + 0.066 * aromatic_atom_fraction",
            "method": "Local linear descriptor heuristic, not the original Delaney ESOL equation.",
        },
        "lipinski_violations": {
            "name": "Lipinski rule violations",
            "units": "count",
            "checks": ["molecular_weight > 500", "logp > 5", "hbd > 5", "hba > 10"],
            "method": "Number of true conditions (0–4).",
        },
        "bioavailability_score": {
            "name": "Bioavailability heuristic score",
            "units": "dimensionless score",
            "method": "First matching rule below; a fixed lookup, not a probability or measured fraction.",
            "ordered_rules": [
                {"condition": "lipinski_violations <= 1 and tpsa <= 140 and rotatable_bonds <= 10", "value": 0.55},
                {"condition": "lipinski_violations <= 2", "value": 0.17},
                {"condition": "True", "value": 0.11},
            ],
        },
    }
    categories = (
        ("human_intestinal_absorption", "Human intestinal absorption", [
            "molecular_weight <= 500", "-1.0 <= logp <= 5.5", "tpsa <= 140",
            "hbd <= 5", "hba <= 10", "rotatable_bonds <= 10",
        ], 5 / 6, 4 / 6),
        ("bbb_permeability", "Blood-brain barrier permeability", [
            "molecular_weight <= 450", "1.0 <= logp <= 4.0", "tpsa <= 90",
            "hbd <= 3", "formal_charge == 0",
        ], 0.8, 0.6),
        ("caco2_permeability", "Caco-2 permeability", [
            "logp >= 0.0", "tpsa <= 120", "rotatable_bonds <= 10",
            "hbd <= 3", "abs(formal_charge) <= 1",
        ], 0.8, 0.6),
    )
    for field_name, name, checks, high, moderate in categories:
        score_field = f"{field_name}_score"
        properties[field_name] = {
            "name": name,
            "units": None,
            "checks": checks,
            "score_field": score_field,
            "thresholds": {"high_min": high, "moderate_min": moderate},
            "method": "high if rounded score >= high_min, else moderate if >= moderate_min, else low.",
        }
        properties[score_field] = {
            "name": f"{name} rule score",
            "units": "fraction of passed checks (0–1)",
            "rules_for": field_name,
            "method": "round(number of true checks / total checks, 6); not a probability.",
        }
    return {
        "method": {
            "id": "local-descriptor-heuristics-v1",
            "rdkit_version": rdBase.rdkitVersion,
            "descriptor_methods": "RDKit MolWt, Wildman–Crippen MolLogP, TPSA, Lipinski counts and formal charge. aromatic_atom_fraction = aromatic atoms / heavy atoms.",
            "rounding": "Continuous input descriptors and logS are rounded to six decimals. Categories use the six-decimal score, not the exact fraction.",
        },
        "descriptor_units": {
            "molecular_weight": "g/mol", "logp": "dimensionless log10(octanol/water partition coefficient)",
            "tpsa": "angstrom^2", "hbd": "count", "hba": "count",
            "rotatable_bonds": "count", "formal_charge": "elementary charge",
            "aromatic_atom_fraction": "fraction (0–1)",
        },
        "property_metadata": properties,
        "interpretation": {
            "outputs_are_measurements": False,
            "scores_are_probabilities": False,
            "scope": "Deterministic screening rules on the supplied molecular form. No pH-dependent speciation, experimental validation, confidence intervals or validated applicability domain is provided.",
            "comparison": "These local heuristics differ from trained ADMET-AI endpoints. Do not interpret scores as absorption percentages, measured permeability or clinical bioavailability.",
        },
    }


def _predict_adme(descriptors: dict[str, float | int]) -> dict[str, float | int | str]:
    molecular_weight = float(descriptors["molecular_weight"])
    logp = float(descriptors["logp"])
    tpsa = float(descriptors["tpsa"])
    hbd = int(descriptors["hbd"])
    hba = int(descriptors["hba"])
    rotatable_bonds = int(descriptors["rotatable_bonds"])
    formal_charge = int(descriptors["formal_charge"])
    aromatic_atom_fraction = float(descriptors["aromatic_atom_fraction"])

    logs = round(
        0.16
        - logp
        - (0.0062 * molecular_weight)
        + (0.066 * rotatable_bonds)
        + (0.066 * aromatic_atom_fraction),
        6,
    )
    hia_checks = [
        molecular_weight <= 500,
        -1.0 <= logp <= 5.5,
        tpsa <= 140,
        hbd <= 5,
        hba <= 10,
        rotatable_bonds <= 10,
    ]
    hia_score = round(sum(hia_checks) / len(hia_checks), 6)
    hia_class = _classify_score(hia_score, high=5 / 6, moderate=4 / 6)

    bbb_checks = [
        molecular_weight <= 450,
        1.0 <= logp <= 4.0,
        tpsa <= 90,
        hbd <= 3,
        formal_charge == 0,
    ]
    bbb_score = round(sum(bbb_checks) / len(bbb_checks), 6)
    bbb_class = _classify_score(bbb_score, high=0.8, moderate=0.6)

    caco2_checks = [
        logp >= 0.0,
        tpsa <= 120,
        rotatable_bonds <= 10,
        hbd <= 3,
        abs(formal_charge) <= 1,
    ]
    caco2_score = round(sum(caco2_checks) / len(caco2_checks), 6)
    caco2_class = _classify_score(caco2_score, high=0.8, moderate=0.6)

    lipinski_violations = sum(
        [
            molecular_weight > 500,
            logp > 5,
            hbd > 5,
            hba > 10,
        ]
    )
    veber_pass = tpsa <= 140 and rotatable_bonds <= 10
    if lipinski_violations <= 1 and veber_pass:
        bioavailability_score = 0.55
    elif lipinski_violations <= 2:
        bioavailability_score = 0.17
    else:
        bioavailability_score = 0.11

    return {
        "aqueous_solubility_logS": logs,
        "human_intestinal_absorption": hia_class,
        "human_intestinal_absorption_score": hia_score,
        "caco2_permeability": caco2_class,
        "caco2_permeability_score": caco2_score,
        "bbb_permeability": bbb_class,
        "bbb_permeability_score": bbb_score,
        "bioavailability_score": bioavailability_score,
        "lipinski_violations": lipinski_violations,
    }


def _evaluate_criteria(
    analysis: MoleculeAnalysis,
    *,
    criteria: dict[str, dict[str, object]] | None,
    hypothesis_name: str | None,
) -> dict[str, object] | None:
    if not criteria:
        return None

    metrics = analysis.metric_values()
    checks: list[dict[str, object]] = []
    for metric, expectations in criteria.items():
        if not isinstance(expectations, dict) or not expectations:
            raise ValueError(f"Criteria for metric '{metric}' must be a non-empty mapping.")
        actual = metrics.get(metric)
        supported = True
        details: list[str] = []
        for operator, expected in expectations.items():
            passed = _compare_metric(actual, operator, expected)
            supported = supported and passed
            details.append(f"{operator} {expected!r}")
        checks.append(
            {
                "metric": metric,
                "actual": actual,
                "expected": dict(expectations),
                "supported": supported,
                "details": ", ".join(details),
            }
        )

    satisfied = [check for check in checks if check["supported"]]
    failed = [check for check in checks if not check["supported"]]
    return {
        "hypothesis_name": hypothesis_name or "unnamed hypothesis",
        "criteria_count": len(checks),
        "satisfied_count": len(satisfied),
        "supported": len(failed) == 0,
        "checks": checks,
        "failed_checks": failed,
    }


def _compare_metric(actual: object, operator: str, expected: object) -> bool:
    if operator == "equals":
        return actual == expected
    if operator == "min":
        return actual is not None and actual >= expected
    if operator == "max":
        return actual is not None and actual <= expected
    if operator == "in":
        return actual in expected if isinstance(expected, (list, tuple, set)) else False
    raise ValueError(f"Unsupported criterion operator '{operator}'.")


def _require_valid_smiles(smiles: str) -> tuple[MoleculeAnalysis, Chem.Mol | None]:
    analysis = analyze_smiles(smiles)
    if not analysis.is_valid or analysis.connectivity_smiles is None:
        raise ValueError(f"Invalid SMILES query '{smiles}'.")
    return analysis, Chem.MolFromSmiles(analysis.connectivity_smiles)


def _resolve_field_name(
    row: dict[str, object],
    configured: str | None,
    hints: Iterable[str],
    field_type: str,
    *,
    required: bool = True,
) -> str | None:
    if configured is not None:
        if configured not in row:
            raise MolecularStructureError(
                f"Configured {field_type} field '{configured}' was not found."
            )
        return configured

    normalized = {_normalize_key(key): key for key in row}
    for hint in hints:
        field_name = normalized.get(_normalize_key(hint))
        if field_name is not None:
            return field_name
    if required:
        return None
    return None


def _format_entry_labels(entries: Sequence[MoleculeDatasetEntry]) -> str:
    labels: list[str] = []
    for entry in entries:
        base = entry.compound_name or entry.connectivity_smiles or entry.raw_smiles
        labels.append(f"{base} ({entry.source.label()})")
    return "; ".join(labels)


def _classify_score(score: float, *, high: float, moderate: float) -> str:
    if score >= high:
        return "high"
    if score >= moderate:
        return "moderate"
    return "low"


def _normalize_key(value: str) -> str:
    return "".join(
        character if character.isalnum() else "_" for character in value.strip().lower()
    ).strip("_")


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "ConnectedMoleculeDataset",
    "DatasetReference",
    "MolecularStructureError",
    "MoleculeAnalysis",
    "MoleculeDataset",
    "MoleculeDatasetEntry",
    "SmilesDatasetSource",
    "analyze_smiles",
    "connect_smiles_datasets",
    "profile_molecule_physchem_and_adme_heuristic",
    "smiles_to_inchikey",
]
