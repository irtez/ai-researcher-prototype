"""PubChem integration module for compound search and identifier conversion.

Implements project requirements for:
- search by molecular mass (exact value or range)
- search by molecular formula
- search by SMILES string
- deterministic conversion between common chemistry identifiers via PubChem
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable, TypeVar

import httpx
import pubchempy as pcp

DEFAULT_CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "pubchem.sqlite"
PUBCHEM_CACHE_MODES = {"off", "read_only", "read_write"}

_T = TypeVar("_T")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class _RateLimiter:
    """Process-wide, thread-safe minimum-interval rate limiter.

    PubChem's usage policy asks any single application to stay under ~5 requests
    per second; exceeding it returns HTTP 503 and, if sustained, a temporary IP
    block. The chem MCP server runs each (synchronous) tool call in a FastMCP
    worker thread, and a fresh ``PubChemSearchClient`` is built per call, so the
    limiter must be a shared module-level singleton guarded by a lock rather than
    per-client state. Callers briefly serialize on the lock for the pacing sleep.
    """

    def __init__(self, max_per_second: float) -> None:
        self._min_interval = 1.0 / max_per_second if max_per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        if self._min_interval <= 0.0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self._min_interval


# Shared across all PubChemSearchClient instances / FastMCP worker threads.
_RATE_LIMITER = _RateLimiter(_env_float("PUBCHEM_MAX_RPS", 3.0))


def _is_retryable(exc: Exception) -> bool:
    """Whether a failed external call is a transient condition worth retrying."""
    # pubchempy maps 503/500/504 to these subclasses (create_http_error).
    if isinstance(exc, (pcp.ServerBusyError, pcp.ServerError, pcp.TimeoutError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (500, 502, 503, 504)
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    return False


def _external_request(fn: Callable[[], _T]) -> _T:
    """Run an external PubChem/NCBI call under the global rate limit.

    Transient failures (HTTP 503/500/504, network timeouts) are retried with
    exponential backoff; everything else (e.g. a 404 NotFoundError) propagates
    immediately. Tunable via ``PUBCHEM_MAX_RPS`` / ``PUBCHEM_MAX_RETRIES`` /
    ``PUBCHEM_RETRY_BASE_DELAY``.
    """
    max_retries = _env_int("PUBCHEM_MAX_RETRIES", 3)
    base_delay = _env_float("PUBCHEM_RETRY_BASE_DELAY", 1.0)
    attempt = 0
    while True:
        _RATE_LIMITER.acquire()
        try:
            return fn()
        except Exception as exc:
            if attempt >= max_retries or not _is_retryable(exc):
                raise
            time.sleep(base_delay * (2**attempt))
            attempt += 1


class PubChemIntegrationError(RuntimeError):
    """Raised when PubChem integration cannot complete the requested operation."""


@dataclass(frozen=True)
class CompoundRecord:
    """Normalized compound data returned by PubChem search operations."""

    cid: int | None
    molecular_formula: str | None
    molecular_weight: float | None
    exact_mass: float | None
    smiles: str | None
    iupac_name: str | None
    inchi: str | None
    inchikey: str | None
    pubchem_url: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CompoundConversion:
    """Resolved conversion between chemistry identifier formats."""

    input_value: str
    input_format: str
    cid: int | None
    outputs: dict[str, object]
    pubchem_url: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class PubChemSQLiteCache:
    """Small SQLite cache for normalized PubChem responses."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def get(self, operation: str, request: dict[str, object]) -> object | None:
        if not self.path.exists():
            return None

        key = self._cache_key(operation, request)
        try:
            with sqlite3.connect(self.path, timeout=30.0) as connection:
                row = connection.execute(
                    "SELECT response_json FROM pubchem_cache WHERE cache_key = ?",
                    (key,),
                ).fetchone()
        except sqlite3.Error:
            return None

        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    def set(self, operation: str, request: dict[str, object], response: object) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = self._cache_key(operation, request)
        request_json = self._json_dumps(request)
        response_json = self._json_dumps(response)
        now = datetime.now(UTC).isoformat()

        with sqlite3.connect(self.path, timeout=30.0) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pubchem_cache (
                    cache_key TEXT PRIMARY KEY,
                    operation TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO pubchem_cache (
                    cache_key,
                    operation,
                    request_json,
                    response_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    response_json = excluded.response_json,
                    updated_at = excluded.updated_at
                """,
                (key, operation, request_json, response_json, now, now),
            )

    @classmethod
    def _cache_key(cls, operation: str, request: dict[str, object]) -> str:
        payload = cls._json_dumps({"operation": operation, "request": request})
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _json_dumps(payload: object) -> str:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )


class PubChemSearchClient:
    """Search client for PubChem-based chemistry lookups."""

    NCBI_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    PUBCHEM_COMPOUND_URL = "https://pubchem.ncbi.nlm.nih.gov/compound"
    # TODO: remove, replace with strict checks
    IDENTIFIER_NAMESPACE_ALIASES = {
        "cid": "cid",
        "common_name": "name",
        "formula": "formula",
        "inchi": "inchi",
        "inchi_key": "inchikey",
        "inchikey": "inchikey",
        "iupac": "name",
        "iupac_name": "name",
        "molecular_formula": "formula",
        "name": "name",
        "smiles": "smiles",
    }
    # TODO: remove, replace with strict checks
    REPRESENTATION_FIELD_ALIASES = {
        "cid": "cid",
        "exact_mass": "exact_mass",
        "formula": "molecular_formula",
        "inchi": "inchi",
        "inchi_key": "inchikey",
        "inchikey": "inchikey",
        "iupac": "iupac_name",
        "iupac_name": "iupac_name",
        "molecular_formula": "molecular_formula",
        "molecular_weight": "molecular_weight",
        "name": "iupac_name",
        "pubchem_url": "pubchem_url",
        "smiles": "smiles",
        "url": "pubchem_url",
    }

    def __init__(
        self,
        *,
        default_limit: int = 20,
        timeout: float = 20.0,
        cache_path: str | Path | None = None,
        cache_mode: str | None = None,
    ) -> None:
        self.default_limit = self._validate_limit(default_limit)
        self.timeout = float(timeout)
        self.cache_mode = self._resolve_cache_mode(cache_mode)
        self.cache = (
            None
            if self.cache_mode == "off"
            else PubChemSQLiteCache(self._resolve_cache_path(cache_path))
        )

    def search_by_formula(self, formula: str, *, limit: int | None = None) -> list[CompoundRecord]:
        """Find compounds by molecular formula."""
        effective_limit = self._resolve_limit(limit)
        formula = self._require_non_empty_text(formula, "formula")
        request = {"formula": formula, "limit": effective_limit}

        def fetch() -> list[CompoundRecord]:
            try:
                compounds = _external_request(
                    lambda: pcp.get_compounds(
                        formula,
                        namespace="formula",
                        listkey_count=effective_limit,
                    )
                )
            except Exception as exc:
                raise PubChemIntegrationError(f"PubChem formula search failed for '{formula}'.") from exc
            return self._normalize_records(compounds, effective_limit)

        return self._get_or_fetch_records("search_by_formula", request, fetch)

    def search_by_smiles(
        self,
        smiles: str,
        *,
        limit: int | None = None,
        searchtype: str = "identity",
        **search_options: object,
    ) -> list[CompoundRecord]:
        """Find compounds by SMILES string with identity/substructure/similarity modes."""
        effective_limit = self._resolve_limit(limit)
        smiles = self._require_non_empty_text(smiles, "smiles")
        allowed_searchtypes = {"identity", "substructure", "superstructure", "similarity"}
        if searchtype not in allowed_searchtypes:
            values = ", ".join(sorted(allowed_searchtypes))
            raise ValueError(f"Unsupported searchtype '{searchtype}'. Allowed values: {values}.")

        options = dict(search_options)
        options.setdefault("listkey_count", effective_limit)
        request = {
            "smiles": smiles,
            "limit": effective_limit,
            "searchtype": searchtype,
            "options": options,
        }

        def fetch() -> list[CompoundRecord]:
            try:
                compounds = _external_request(
                    lambda: pcp.get_compounds(
                        smiles,
                        namespace="smiles",
                        searchtype=searchtype,
                        **options,
                    )
                )
            except Exception as exc:
                raise PubChemIntegrationError("PubChem SMILES search failed.") from exc
            return self._normalize_records(compounds, effective_limit)

        return self._get_or_fetch_records("search_by_smiles", request, fetch)

    def search_by_identifier(
        self,
        value: str | int,
        *,
        namespace: str,
        limit: int | None = None,
        searchtype: str = "identity",
        **search_options: object,
    ) -> list[CompoundRecord]:
        """Find compounds by PubChem-supported identifier namespace."""
        normalized_namespace = self._normalize_identifier_namespace(namespace)
        effective_limit = self._resolve_limit(limit)

        if normalized_namespace == "cid":
            return self.get_compounds_by_cids([value], limit=effective_limit)
        if normalized_namespace == "formula":
            if not isinstance(value, str):
                raise ValueError("formula must be a non-empty string.")
            return self.search_by_formula(value, limit=effective_limit)
        if normalized_namespace == "smiles":
            if not isinstance(value, str):
                raise ValueError("smiles must be a non-empty string.")
            return self.search_by_smiles(
                value,
                limit=effective_limit,
                searchtype=searchtype,
                **search_options,
            )
        if searchtype != "identity":
            raise ValueError("searchtype is only supported for SMILES queries.")
        if not isinstance(value, str):
            raise ValueError(f"{namespace} must be a non-empty string.")

        identifier = self._require_non_empty_text(value, normalized_namespace)
        options = dict(search_options)
        options.setdefault("listkey_count", effective_limit)
        request = {
            "value": identifier,
            "namespace": normalized_namespace,
            "limit": effective_limit,
            "options": options,
        }

        def fetch() -> list[CompoundRecord]:
            try:
                compounds = _external_request(
                    lambda: pcp.get_compounds(
                        identifier,
                        namespace=normalized_namespace,
                        **options,
                    )
                )
            except Exception as exc:
                raise PubChemIntegrationError(
                    f"PubChem search failed for namespace '{normalized_namespace}'."
                ) from exc
            return self._normalize_records(compounds, effective_limit)

        return self._get_or_fetch_records("search_by_identifier", request, fetch)

    def convert_identifier(
        self,
        value: str | int,
        *,
        input_format: str,
        output_formats: Iterable[str] | None = None,
    ) -> CompoundConversion | None:
        """Convert a chemistry identifier into one or more alternate representations."""
        normalized_input = self._normalize_identifier_namespace(input_format)
        requested_outputs = list(
            output_formats
            if output_formats is not None
            else ("smiles", "iupac_name", "inchi", "inchikey", "molecular_formula")
        )
        normalized_outputs = [
            (name, self._normalize_representation_field(name)) for name in requested_outputs
        ]
        records = self.search_by_identifier(value, namespace=normalized_input, limit=1)
        if not records:
            return None

        record = records[0]
        return CompoundConversion(
            input_value=str(value),
            input_format=normalized_input,
            cid=record.cid,
            outputs={name: getattr(record, field) for name, field in normalized_outputs},
            pubchem_url=record.pubchem_url,
        )

    def get_computed_properties(
        self,
        value: str | int,
        *,
        input_format: str = "name",
    ) -> dict[str, object] | None:
        """Fetch PubChem-computed physicochemical properties for one compound.

        Returns PubChem's own values (XLogP3, TPSA, H-bond counts, rotatable bonds,
        molecular weight, exact mass, formal charge, complexity). These are the
        same source most literature/PubChem-reported descriptors come from, so a
        descriptor claim sourced from PubChem can be verified against PubChem rather
        than against an RDKit recomputation that follows a different TPSA/logP
        convention.
        """
        namespace = self._normalize_identifier_namespace(input_format)
        records = self.search_by_identifier(value, namespace=namespace, limit=1)
        if not records or records[0].cid is None:
            return None
        cid = int(records[0].cid)
        try:
            rows = _external_request(
                lambda: pcp.get_properties(
                    [
                        "XLogP",
                        "TPSA",
                        "HBondDonorCount",
                        "HBondAcceptorCount",
                        "RotatableBondCount",
                        "MolecularWeight",
                        "ExactMass",
                        "Charge",
                        "Complexity",
                    ],
                    cid,
                    namespace="cid",
                )
            )
        except Exception as exc:
            raise PubChemIntegrationError(f"PubChem property lookup failed for CID {cid}.") from exc
        if not rows:
            return None
        raw = rows[0]
        return {
            "cid": cid,
            "xlogp3": self._to_float(raw.get("XLogP")),
            "tpsa": self._to_float(raw.get("TPSA")),
            "hbd": self._to_int(raw.get("HBondDonorCount")),
            "hba": self._to_int(raw.get("HBondAcceptorCount")),
            "rotatable_bonds": self._to_int(raw.get("RotatableBondCount")),
            "molecular_weight": self._to_float(raw.get("MolecularWeight")),
            "exact_mass": self._to_float(raw.get("ExactMass")),
            "formal_charge": self._to_int(raw.get("Charge")),
            "complexity": self._to_float(raw.get("Complexity")),
            "source": "pubchem",
            "pubchem_url": f"{self.PUBCHEM_COMPOUND_URL}/{cid}",
        }

    def search_by_mass(
        self,
        *,
        exact_mass: float | None = None,
        min_mass: float | None = None,
        max_mass: float | None = None,
        tolerance: float = 0.0,
        limit: int | None = None,
    ) -> list[CompoundRecord]:
        """Find compounds by exact mass value or exact-mass range."""
        effective_limit = self._resolve_limit(limit)
        low, high = self._resolve_mass_bounds(
            exact_mass=exact_mass,
            min_mass=min_mass,
            max_mass=max_mass,
            tolerance=tolerance,
        )
        request = {"min_mass": low, "max_mass": high, "limit": effective_limit}

        def fetch() -> list[CompoundRecord]:
            cids = self._search_cids_by_exact_mass(low, high, effective_limit)
            return self.get_compounds_by_cids(cids, limit=effective_limit)

        return self._get_or_fetch_records("search_by_mass", request, fetch)

    def get_compounds_by_cids(
        self,
        cids: Iterable[int | str],
        *,
        limit: int | None = None,
    ) -> list[CompoundRecord]:
        """Load compounds from PubChem by CID list."""
        effective_limit = self._resolve_limit(limit)
        cleaned: list[int] = []
        for cid in cids:
            try:
                cleaned.append(int(cid))
            except (TypeError, ValueError):
                continue

        if not cleaned:
            return []

        cleaned = cleaned[:effective_limit]
        request = {"cids": cleaned, "limit": effective_limit}

        def fetch() -> list[CompoundRecord]:
            try:
                compounds = _external_request(
                    lambda: pcp.get_compounds(cleaned, namespace="cid")
                )
            except Exception as exc:
                raise PubChemIntegrationError("Unable to load compounds for CID list.") from exc
            return self._normalize_records(compounds, effective_limit)

        return self._get_or_fetch_records("get_compounds_by_cids", request, fetch)

    def _search_cids_by_exact_mass(self, min_mass: float, max_mass: float, limit: int) -> list[int]:
        """Use NCBI E-utilities for CID lookup by exact-mass range."""
        term = f"{min_mass:.6f}:{max_mass:.6f}[exactmass]"
        params = {
            "db": "pccompound",
            "term": term,
            "retmode": "json",
            "retmax": str(limit),
        }

        payload = self._fetch_json(self.NCBI_ESEARCH_URL, params=params)
        idlist = payload.get("esearchresult", {}).get("idlist", [])
        cids: list[int] = []
        for value in idlist:
            try:
                cids.append(int(value))
            except (TypeError, ValueError):
                continue
        return cids

    def _fetch_json(self, url: str, *, params: dict[str, str] | None = None) -> dict[str, object]:
        def _do() -> httpx.Response:
            response = httpx.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            return response

        try:
            response = _external_request(_do)
        except httpx.HTTPStatusError as exc:
            raise PubChemIntegrationError(
                f"PubChem request failed with HTTP {exc.response.status_code}."
            ) from exc
        except httpx.RequestError as exc:
            raise PubChemIntegrationError(f"PubChem request failed: {exc}.") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise PubChemIntegrationError("Received malformed JSON from PubChem.") from exc

        if not isinstance(data, dict):
            raise PubChemIntegrationError("Unexpected PubChem response format.")
        return data

    @staticmethod
    def _compound_to_record(compound: object) -> CompoundRecord:
        cid = PubChemSearchClient._to_int(PubChemSearchClient._get(compound, "cid"))
        return CompoundRecord(
            cid=cid,
            molecular_formula=PubChemSearchClient._to_str(
                PubChemSearchClient._get(compound, "molecular_formula")
            ),
            molecular_weight=PubChemSearchClient._to_float(
                PubChemSearchClient._get(compound, "molecular_weight")
            ),
            exact_mass=PubChemSearchClient._to_float(PubChemSearchClient._get(compound, "exact_mass")),
            smiles=PubChemSearchClient._first_non_empty_str(
                PubChemSearchClient._get(compound, "smiles"),
                PubChemSearchClient._get(compound, "connectivity_smiles"),
                PubChemSearchClient._get(compound, "smiles"),
                PubChemSearchClient._get(compound, "connectivity_smiles"),
            ),
            iupac_name=PubChemSearchClient._to_str(PubChemSearchClient._get(compound, "iupac_name")),
            inchi=PubChemSearchClient._to_str(PubChemSearchClient._get(compound, "inchi")),
            inchikey=PubChemSearchClient._to_str(PubChemSearchClient._get(compound, "inchikey")),
            pubchem_url=(
                f"{PubChemSearchClient.PUBCHEM_COMPOUND_URL}/{cid}" if cid is not None else None
            ),
        )

    def _normalize_records(self, compounds: Iterable[object], limit: int) -> list[CompoundRecord]:
        records = [self._compound_to_record(compound) for compound in compounds]
        return records[:limit]

    def _get_or_fetch_records(
        self,
        operation: str,
        request: dict[str, object],
        fetch: Callable[[], list[CompoundRecord]],
    ) -> list[CompoundRecord]:
        cached_records = self._read_cached_records(operation, request)
        if cached_records is not None:
            return cached_records

        if self.cache_mode == "read_only":
            raise PubChemIntegrationError(
                f"No cached PubChem result for {operation}; cache mode is read_only."
            )

        records = fetch()
        self._write_cached_records(operation, request, records)
        return records

    def _read_cached_records(
        self,
        operation: str,
        request: dict[str, object],
    ) -> list[CompoundRecord] | None:
        if self.cache is None:
            return None

        payload = self.cache.get(operation, request)
        if payload is None:
            return None
        if not isinstance(payload, list):
            return None

        try:
            return [
                CompoundRecord(**record)
                for record in payload
                if isinstance(record, dict)
            ]
        except TypeError:
            return None

    def _write_cached_records(
        self,
        operation: str,
        request: dict[str, object],
        records: list[CompoundRecord],
    ) -> None:
        if self.cache is None:
            return

        payload = [record.to_dict() for record in records]
        try:
            self.cache.set(operation, request, payload)
        except (OSError, sqlite3.Error):
            return

    @staticmethod
    def _resolve_mass_bounds(
        *,
        exact_mass: float | None,
        min_mass: float | None,
        max_mass: float | None,
        tolerance: float,
    ) -> tuple[float, float]:
        if tolerance < 0:
            raise ValueError("tolerance must be >= 0.")

        if exact_mass is not None:
            if min_mass is not None or max_mass is not None:
                raise ValueError("Use exact_mass OR min_mass/max_mass, not both.")
            center = float(exact_mass)
            low = center - tolerance
            high = center + tolerance
        else:
            if min_mass is None or max_mass is None:
                raise ValueError("Set exact_mass or set both min_mass and max_mass.")
            low = float(min_mass)
            high = float(max_mass)

        if low < 0 or high < 0:
            raise ValueError("Mass bounds cannot be negative.")
        if low > high:
            raise ValueError("min_mass cannot be greater than max_mass.")
        return low, high

    def _resolve_limit(self, limit: int | None) -> int:
        if limit is None:
            return self.default_limit
        return self._validate_limit(limit)

    @classmethod
    def _normalize_identifier_namespace(cls, namespace: str) -> str:
        key = cls._require_non_empty_text(namespace, "namespace").lower()
        normalized = cls.IDENTIFIER_NAMESPACE_ALIASES.get(key)
        if normalized is None:
            values = ", ".join(sorted(cls.IDENTIFIER_NAMESPACE_ALIASES))
            raise ValueError(f"Unsupported namespace '{namespace}'. Allowed values: {values}.")
        return normalized

    @classmethod
    def _normalize_representation_field(cls, name: str) -> str:
        key = cls._require_non_empty_text(name, "output_format").lower()
        normalized = cls.REPRESENTATION_FIELD_ALIASES.get(key)
        if normalized is None:
            values = ", ".join(sorted(cls.REPRESENTATION_FIELD_ALIASES))
            raise ValueError(f"Unsupported output_format '{name}'. Allowed values: {values}.")
        return normalized

    @staticmethod
    def _validate_limit(limit: int) -> int:
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer.")
        return limit

    @staticmethod
    def _resolve_cache_mode(cache_mode: str | None) -> str:
        value = cache_mode if cache_mode is not None else os.environ.get("PUBCHEM_CACHE_MODE")
        mode = (value or "read_write").strip().lower().replace("-", "_")
        if mode not in PUBCHEM_CACHE_MODES:
            values = ", ".join(sorted(PUBCHEM_CACHE_MODES))
            raise ValueError(f"Unsupported PubChem cache mode '{value}'. Allowed values: {values}.")
        return mode

    @staticmethod
    def _resolve_cache_path(cache_path: str | Path | None) -> Path:
        if cache_path is not None:
            return Path(cache_path).expanduser()

        env_path = os.environ.get("PUBCHEM_CACHE_PATH")
        if env_path:
            return Path(env_path).expanduser()
        return DEFAULT_CACHE_PATH

    @staticmethod
    def _require_non_empty_text(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string.")
        return value.strip()

    @staticmethod
    def _get(obj: object, name: str) -> object:
        return getattr(obj, name, None)

    @staticmethod
    def _to_float(value: object) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_int(value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_str(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _first_non_empty_str(*values: object) -> str | None:
        for value in values:
            text = PubChemSearchClient._to_str(value)
            if text is not None:
                return text
        return None


__all__ = [
    "CompoundConversion",
    "CompoundRecord",
    "PubChemIntegrationError",
    "PubChemSearchClient",
]
