"""Tool-friendly wrappers for ADMET-AI backed ADME prediction."""

from __future__ import annotations

from typing import Any

from adme_prediction import (
    ADMET_AI_ADME_FIELDS,
    ADMEPrediction,
    ADMEPredictionError,
    profile_molecule_physchem_and_adme_nn,
)
from adme_metadata import INTERPRETATION, get_model_info, get_property_metadata, prediction_warnings


def _error_payload(error: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "error_type": type(error).__name__,
        "error": str(error),
    }


def _prediction_results(predictions: ADMEPrediction | list[ADMEPrediction]) -> list[dict[str, object]]:
    if isinstance(predictions, ADMEPrediction):
        return [predictions.to_dict()]
    return [prediction.to_dict() for prediction in predictions]


def tool_profile_molecule_physchem_and_adme_nn(
    molecules: list[str],
) -> dict[str, Any]:
    """Calculate physicochemical descriptors and predict ADME properties.

    The ADMET-AI neural-network model supplies model-based estimates and may
    load substantial model resources on first use.
    Returns model version/weights identity, per-endpoint names, units, task types
    and source URLs from the installed library, plus cached and prediction_warnings
    per molecule. Read property_metadata and interpretation before drawing
    conclusions: classification scores are not measured absorption percentages;
    cached repeats are not independent calculations. Values are never clamped
    or silently converted when the metadata indicates a range or scale issue.

    Descriptors: molecular_weight, logp, tpsa, hbd, hba,
    rotatable_bonds, formal_charge, ring_count, aromatic_ring_count,
    heavy_atom_count, fraction_csp3, aromatic_atom_fraction.
    ADME properties: Solubility_AqSolDB, HIA_Hou, Caco2_Wang,
    BBB_Martins, Pgp_Broccatelli, PPBR_AZ, VDss_Lombardo,
    Clearance_Hepatocyte_AZ, Clearance_Microsome_AZ, Bioavailability_Ma,
    CYP1A2_Veith, CYP2C9_Veith, CYP2C19_Veith, CYP2D6_Veith,
    CYP3A4_Veith.
    """
    try:
        predictions = profile_molecule_physchem_and_adme_nn(
            molecules,
        )
        results = _prediction_results(predictions)
        metadata = get_property_metadata(ADMET_AI_ADME_FIELDS)
        for result in results:
            result["prediction_warnings"] = prediction_warnings(result["adme_predictions"], metadata)
        return {
            "ok": True,
            "results": results,
            "model": dict(get_model_info()),
            "property_metadata": metadata,
            "interpretation": dict(INTERPRETATION),
        }
    except (ValueError, ADMEPredictionError) as error:
        return _error_payload(error)


TOOL_REGISTRY = {
    "profile_molecule_physchem_and_adme_nn": tool_profile_molecule_physchem_and_adme_nn,
}


__all__ = [
    "TOOL_REGISTRY",
    "tool_profile_molecule_physchem_and_adme_nn",
]
