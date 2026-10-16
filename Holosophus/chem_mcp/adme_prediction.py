"""ADMET-AI backed ADME prediction with per-molecule SQLite caching."""

from __future__ import annotations

import contextlib
import io
import json
import logging
import math
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence, cast

from admet_ai import ADMETModel
from rdkit import Chem, RDLogger
from rdkit.Chem import Descriptors, rdMolDescriptors

from molecular_structure_processing import _calculate_descriptors
from adme_metadata import get_model_info

RDLogger.DisableLog("rdApp.error")

DEFAULT_ADME_CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "admet_ai.sqlite"
_ADMET_MODEL: Any | None = None

ADMET_AI_ADME_FIELDS = (
    "Solubility_AqSolDB",
    "HIA_Hou",
    "Caco2_Wang",
    "BBB_Martins",
    "Pgp_Broccatelli",
    "PPBR_AZ",
    "VDss_Lombardo",
    "Clearance_Hepatocyte_AZ",
    "Clearance_Microsome_AZ",
    "Bioavailability_Ma",
    "CYP1A2_Veith",
    "CYP2C9_Veith",
    "CYP2C19_Veith",
    "CYP2D6_Veith",
    "CYP3A4_Veith",
)


class ADMEPredictionError(RuntimeError):
    """Raised when ADMET-AI prediction cannot complete."""


@dataclass(frozen=True)
class ADMEPrediction:
    """Normalized ADMET-AI prediction result for one SMILES molecule."""

    input_smiles: str
    connectivity_smiles: str | None
    is_valid: bool
    molecular_formula: str | None
    molecular_weight: float | None
    exact_mass: float | None
    descriptors: dict[str, float | int]
    adme_predictions: dict[str, float | int | str | bool | None]
    errors: tuple[str, ...] = ()
    cached: bool = False

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
            "cached": self.cached,
        }


@dataclass(frozen=True)
class _PreparedMolecule:
    input_smiles: str
    cache_key: str
    connectivity_smiles: str | None
    is_valid: bool
    profile: ADMEPrediction
    errors: tuple[str, ...] = ()


class ADMESQLiteCache:
    """Small SQLite cache for normalized ADMET-AI predictions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def get(self, *, molecule_key: str) -> dict[str, float | int | str | bool | None] | None:
        if not self.path.exists():
            return None

        try:
            with sqlite3.connect(self.path, timeout=30.0) as connection:
                row = connection.execute(
                    """
                    SELECT response_json
                    FROM adme_prediction_cache
                    WHERE molecule_key = ?
                    """,
                    (molecule_key,),
                ).fetchone()
        except sqlite3.Error:
            return None

        if row is None:
            return None

        try:
            payload = json.loads(row[0])
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return None
        return cast(dict[str, float | int | str | bool | None], payload)

    def set(
        self,
        *,
        molecule_key: str,
        connectivity_smiles: str,
        adme_predictions: dict[str, float | int | str | bool | None],
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        response_json = _json_dumps(adme_predictions)
        now = datetime.now(UTC).isoformat()

        with sqlite3.connect(self.path, timeout=30.0) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS adme_prediction_cache (
                    molecule_key TEXT NOT NULL,
                    connectivity_smiles TEXT,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (molecule_key)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO adme_prediction_cache (
                    molecule_key,
                    connectivity_smiles,
                    response_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(molecule_key) DO UPDATE SET
                    connectivity_smiles = excluded.connectivity_smiles,
                    response_json = excluded.response_json,
                    updated_at = excluded.updated_at
                """,
                (
                    molecule_key,
                    connectivity_smiles,
                    response_json,
                    now,
                    now,
                ),
            )


def profile_molecule_physchem_and_adme_nn(
    molecules: str | Sequence[str],
    *,
    cache_path: str | Path | None = None,
) -> ADMEPrediction | list[ADMEPrediction]:
    """Profile one molecule or a batch with physchem descriptors and ADMET-AI ADME.

    A string input returns one :class:`ADMEPrediction`. A list or tuple returns a
    list in the same order as the input molecules.
    """
    smiles_values, single_input = _coerce_molecules(molecules)
    cache = ADMESQLiteCache(cache_path or DEFAULT_ADME_CACHE_PATH)

    prepared = [_prepare_molecule(smiles) for smiles in smiles_values]
    try:
        model_id = get_model_info()["id"]
    except (ImportError, OSError, ValueError) as exc:
        raise ADMEPredictionError("Cannot identify the installed ADMET-AI model for prediction caching.") from exc
    # Unversioned legacy rows remain untouched, but cannot be attributed to the
    # current model. A package or weights change gets a separate cache namespace.
    prepared = [replace(molecule, cache_key=f"v2:{model_id}:{molecule.cache_key}") for molecule in prepared]
    results_by_position: dict[int, ADMEPrediction] = {}
    misses_by_key: dict[str, _PreparedMolecule] = {}

    for index, molecule in enumerate(prepared):
        cached_prediction = cache.get(molecule_key=molecule.cache_key)
        if cached_prediction is not None:
            results_by_position[index] = replace(
                molecule.profile,
                adme_predictions=cached_prediction,
                cached=True,
            )
            continue

        if not molecule.is_valid:
            prediction = molecule.profile
            results_by_position[index] = prediction
            continue

        misses_by_key.setdefault(molecule.cache_key, molecule)

    if misses_by_key:
        smiles_to_predict = [
            molecule.connectivity_smiles
            for molecule in misses_by_key.values()
            if molecule.connectivity_smiles is not None
        ]
        model_predictions = _predict_uncached(smiles_to_predict)
        for molecule in misses_by_key.values():
            assert molecule.connectivity_smiles is not None
            predictions = model_predictions.get(molecule.connectivity_smiles)
            if predictions is None:
                prediction = replace(
                    molecule.profile,
                    adme_predictions={},
                    errors=(
                        *molecule.profile.errors,
                        "ADMET-AI returned no prediction for this molecule.",
                    ),
                )
            else:
                prediction = replace(
                    molecule.profile,
                    adme_predictions=predictions,
                )
                cache.set(
                    molecule_key=molecule.cache_key,
                    connectivity_smiles=molecule.connectivity_smiles,
                    adme_predictions=predictions,
                )

        for index, molecule in enumerate(prepared):
            if index in results_by_position:
                continue
            cached_prediction = cache.get(molecule_key=molecule.cache_key)
            if cached_prediction is None:
                raise ADMEPredictionError(
                    f"Failed to cache ADMET-AI prediction for '{molecule.input_smiles}'."
                )
            results_by_position[index] = replace(
                molecule.profile,
                adme_predictions=cached_prediction,
            )

    results = [results_by_position[index] for index in range(len(prepared))]
    if single_input:
        return results[0]
    return results


def _coerce_molecules(molecules: str | Sequence[str]) -> tuple[list[str], bool]:
    if isinstance(molecules, str):
        smiles = molecules.strip()
        if not smiles:
            raise ValueError("smiles must be a non-empty string.")
        return [smiles], True

    if not isinstance(molecules, Sequence):
        raise ValueError("molecules must be a SMILES string or a sequence of SMILES strings.")
    if not molecules:
        raise ValueError("molecules must contain at least one SMILES string.")

    smiles_values: list[str] = []
    for index, value in enumerate(molecules):
        if not isinstance(value, str):
            raise ValueError(f"molecules[{index}] must be a SMILES string.")
        smiles = value.strip()
        if not smiles:
            raise ValueError(f"molecules[{index}] must be a non-empty SMILES string.")
        smiles_values.append(smiles)
    return smiles_values, False


def _prepare_molecule(smiles: str) -> _PreparedMolecule:
    input_smiles = smiles.strip()
    mol = Chem.MolFromSmiles(input_smiles)
    if mol is None:
        raise ValueError(f"{smiles} is invalid SMILES structure.")

    canonical = Chem.MolToSmiles(mol, canonical=True)
    profile = ADMEPrediction(
        input_smiles=input_smiles,
        connectivity_smiles=canonical,
        is_valid=True,
        molecular_formula=rdMolDescriptors.CalcMolFormula(mol),
        molecular_weight=round(float(Descriptors.MolWt(mol)), 6),
        exact_mass=round(float(Descriptors.ExactMolWt(mol)), 6),
        descriptors=_calculate_descriptors(mol),
        adme_predictions={},
        errors=(),
    )
    return _PreparedMolecule(
        input_smiles=input_smiles,
        cache_key=f"smiles:{canonical}",
        connectivity_smiles=canonical,
        is_valid=True,
        profile=profile,
        errors=(),
    )


def _predict_uncached(smiles_values: list[str]) -> dict[str, dict[str, float | int | str | bool | None]]:
    if not smiles_values:
        return {}
    captured_output = io.StringIO()
    logging_was_disabled = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(captured_output), contextlib.redirect_stderr(captured_output):
            model = _get_model()
            raw_predictions = model.predict(smiles=smiles_values)
    except Exception as exc:
        raise ADMEPredictionError("ADMET-AI prediction failed.") from exc
    finally:
        logging.disable(logging_was_disabled)
    return _normalize_model_predictions(raw_predictions, smiles_values)


def _get_model() -> Any:
    global _ADMET_MODEL
    if _ADMET_MODEL is not None:
        return _ADMET_MODEL
    _ADMET_MODEL = ADMETModel()
    return _ADMET_MODEL


def _normalize_model_predictions(
    raw_predictions: object,
    smiles_values: list[str],
) -> dict[str, dict[str, float | int | str | bool | None]]:
    if hasattr(raw_predictions, "iterrows"):
        predictions: dict[str, dict[str, float | int | str | bool | None]] = {}
        for smiles, row in raw_predictions.iterrows():
            predictions[str(smiles)] = _select_adme_predictions(row.to_dict())
        return predictions

    if isinstance(raw_predictions, dict) and len(smiles_values) == 1:
        return {smiles_values[0]: _select_adme_predictions(raw_predictions)}

    raise ADMEPredictionError("Unexpected ADMET-AI prediction output format.")


def _clean_prediction_value(value: object) -> float | int | str | bool | None:
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)


def _select_adme_predictions(raw_predictions: dict[object, object]) -> dict[str, float | int | str | bool | None]:
    return {
        field: _clean_prediction_value(raw_predictions[field])
        for field in ADMET_AI_ADME_FIELDS
        if field in raw_predictions
    }


def _json_dumps(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


__all__ = [
    "ADMEPrediction",
    "ADMEPredictionError",
    "ADMESQLiteCache",
    "DEFAULT_ADME_CACHE_PATH",
    "profile_molecule_physchem_and_adme_nn",
]
