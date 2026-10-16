"""Metadata from the installed ADMET-AI distribution, without running a model."""

from __future__ import annotations

from functools import lru_cache
import hashlib
from importlib.metadata import version
import math
from pathlib import Path
from typing import Any, Iterable


INTERPRETATION = {
    "classification": "Model probability score for the endpoint's positive class (0–1), not a measured fraction absorbed, percent bioavailability or clinical probability. Calibration is not established for the queried molecules.",
    "regression": "Model estimate in the endpoint's declared scale. A null range bound means no finite bound is supplied by the catalog, not a validated applicability domain.",
    "cached": "cached=true means a stored prediction was reused; it is not an independent model evaluation or a reproducibility test.",
    "scope": "Predictions are not measurements. No task-specific accuracy, confidence interval or clinical conclusion is supplied. Raw values are retained when warnings are present.",
}


def fingerprint_weights(directory: Path) -> str:
    paths = sorted(
        path
        for path in directory.rglob("*")
        if path.suffix in {".pt", ".ckpt"} and path.is_file()
    )
    if not paths:
        raise ValueError("No bundled ADMET-AI weights found.")
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(directory).as_posix().encode() + b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def get_model_info() -> dict[str, str]:
    from admet_ai.constants import DEFAULT_MODELS_DIR

    package_version = version("admet-ai")
    fingerprint = fingerprint_weights(Path(DEFAULT_MODELS_DIR))
    return {
        "name": "ADMET-AI bundled default ensemble",
        "package_version": package_version,
        "weights_sha256": fingerprint,
        "id": f"admet-ai:{package_version}:sha256:{fingerprint}",
    }


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    return text if text and text != "-" else None


def get_property_metadata(fields: Iterable[str]) -> dict[str, dict[str, Any]]:
    try:
        from admet_ai.admet_info import get_admet_info

        rows = {
            str(row["id"]): row for row in get_admet_info().to_dict(orient="records")
        }
    except (ImportError, OSError, KeyError, ValueError):
        rows = {}
    metadata = {}
    for field in fields:
        row = rows.get(field)
        if row is None:
            metadata[field] = {
                "available": False,
                "warning": "No installed catalog metadata; do not infer units or endpoint meaning from the identifier alone.",
            }
            continue
        item = {
            "available": True,
            "name": _text(row.get("name")),
            "category": _text(row.get("category")),
            "task_type": _text(row.get("task_type")),
            "units": _text(row.get("units")),
            "range": {
                "minimum": _finite(row.get("minimum")),
                "maximum": _finite(row.get("maximum")),
            },
            "source_url": _text(row.get("url")),
            "metadata_source": "installed admet_ai/resources/data/admet.csv",
        }
        # ADMET-AI 2.0.1 ships this ambiguous label while its raw predictions
        # are on the training-target scale. Preserve both label and number;
        # do not silently apply a 10^6 conversion or infer cm/s from magnitude.
        if field == "Caco2_Wang" and item["units"] == "log(10^-6 cm/s)":
            item["units_warning"] = (
                "The library's logarithmic unit label needs verification against the training target before conversion. "
                "Report the raw model score and its declared label; do not assume log(cm/s) or apply a unit conversion from magnitude alone."
            )
        metadata[field] = item
    return metadata


def prediction_warnings(
    values: dict[str, Any], metadata: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    warnings = []
    for field, value in values.items():
        item = metadata.get(field, {})
        if not item.get("available"):
            warnings.append(
                {
                    "property": field,
                    "code": "metadata_unavailable",
                    "message": "Endpoint meaning and units are not available from the installed catalog.",
                }
            )
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            warnings.append(
                {
                    "property": field,
                    "code": "missing_or_non_numeric",
                    "message": "No finite numeric prediction; do not interpret this value.",
                }
            )
            continue
        bounds = item.get("range", {})
        lower, upper = bounds.get("minimum"), bounds.get("maximum")
        if (lower is not None and value < lower) or (
            upper is not None and value > upper
        ):
            warnings.append(
                {
                    "property": field,
                    "code": "outside_declared_range",
                    "value": value,
                    "message": "Outside the range declared by the installed catalog. Raw value retained; do not interpret it as a physically valid quantity or probability without checking the model's target scale.",
                }
            )
    return warnings
