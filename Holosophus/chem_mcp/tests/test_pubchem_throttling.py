"""Tests for PubChem rate limiting + transient-error retry (offline, no network)."""

import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

import httpx
import pubchempy as pcp

import pubchem_integration
from pubchem_integration import (
    PubChemIntegrationError,
    PubChemSearchClient,
    _external_request,
    _is_retryable,
    _RateLimiter,
)


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


class RateLimiterTests(unittest.TestCase):
    def test_zero_rate_disables_pacing(self) -> None:
        limiter = _RateLimiter(max_per_second=0.0)
        with patch("pubchem_integration.time.sleep") as sleep:
            limiter.acquire()
            limiter.acquire()
        sleep.assert_not_called()

    def test_paces_consecutive_acquires_by_min_interval(self) -> None:
        limiter = _RateLimiter(max_per_second=2.0)  # min_interval = 0.5s
        # acquire #1: monotonic=100.0 -> no wait; acquire #2: monotonic=100.1 ->
        # wait 0.4s, then monotonic=100.5 after the sleep.
        with patch("pubchem_integration.time.monotonic", side_effect=[100.0, 100.1, 100.5]), patch(
            "pubchem_integration.time.sleep"
        ) as sleep:
            limiter.acquire()
            limiter.acquire()
        sleep.assert_called_once()
        (waited,), _ = sleep.call_args
        self.assertAlmostEqual(waited, 0.4, places=6)


class IsRetryableTests(unittest.TestCase):
    def test_pubchem_transient_errors_are_retryable(self) -> None:
        self.assertTrue(_is_retryable(pcp.ServerBusyError(503, "busy", [])))
        self.assertTrue(_is_retryable(pcp.ServerError(500, "err", [])))
        self.assertTrue(_is_retryable(pcp.TimeoutError(504, "timeout", [])))

    def test_pubchem_client_errors_are_not_retryable(self) -> None:
        self.assertFalse(_is_retryable(pcp.NotFoundError(404, "missing", [])))
        self.assertFalse(_is_retryable(pcp.BadRequestError(400, "bad", [])))

    def test_httpx_5xx_and_network_errors_are_retryable(self) -> None:
        request = httpx.Request("GET", "https://example.test")
        resp503 = httpx.Response(status_code=503, request=request)
        resp404 = httpx.Response(status_code=404, request=request)
        self.assertTrue(
            _is_retryable(httpx.HTTPStatusError("busy", request=request, response=resp503))
        )
        self.assertFalse(
            _is_retryable(httpx.HTTPStatusError("missing", request=request, response=resp404))
        )
        self.assertTrue(_is_retryable(httpx.ConnectTimeout("timeout", request=request)))


class ExternalRequestTests(unittest.TestCase):
    def test_retries_transient_then_succeeds(self) -> None:
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise pcp.ServerBusyError(503, "busy", [])
            return "ok"

        with patch("pubchem_integration.time.sleep") as sleep, patch.dict(
            "os.environ", {"PUBCHEM_MAX_RETRIES": "3", "PUBCHEM_RETRY_BASE_DELAY": "1.0"}
        ):
            result = _external_request(fn)

        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 3)
        # exponential backoff between the two failed attempts: 1.0s, then 2.0s.
        self.assertIn(call(1.0), sleep.call_args_list)
        self.assertIn(call(2.0), sleep.call_args_list)

    def test_non_retryable_raises_immediately(self) -> None:
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            raise pcp.NotFoundError(404, "missing", [])

        with patch("pubchem_integration.time.sleep"):
            with self.assertRaises(pcp.NotFoundError):
                _external_request(fn)
        self.assertEqual(calls["n"], 1)

    def test_exhausts_retries_then_raises(self) -> None:
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            raise pcp.ServerBusyError(503, "busy", [])

        with patch("pubchem_integration.time.sleep"), patch.dict(
            "os.environ", {"PUBCHEM_MAX_RETRIES": "2"}
        ):
            with self.assertRaises(pcp.ServerBusyError):
                _external_request(fn)
        # 1 initial attempt + 2 retries.
        self.assertEqual(calls["n"], 3)


class ClientIntegrationTests(unittest.TestCase):
    def test_search_by_formula_retries_then_returns_records(self) -> None:
        client = PubChemSearchClient(cache_mode="off")
        attempts = {"n": 0}

        def flaky_get_compounds(*args: object, **kwargs: object) -> list[SimpleNamespace]:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise pcp.ServerBusyError(503, "busy", [])
            return [_compound()]

        with patch("pubchem_integration.pcp.get_compounds", side_effect=flaky_get_compounds), patch(
            "pubchem_integration.time.sleep"
        ):
            records = client.search_by_formula("C9H8O4", limit=1)

        self.assertEqual(attempts["n"], 2)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].cid, 2244)

    def test_search_by_formula_surfaces_persistent_failure(self) -> None:
        client = PubChemSearchClient(cache_mode="off")

        def always_busy(*args: object, **kwargs: object) -> list[SimpleNamespace]:
            raise pcp.ServerBusyError(503, "busy", [])

        with patch("pubchem_integration.pcp.get_compounds", side_effect=always_busy), patch(
            "pubchem_integration.time.sleep"
        ), patch.dict("os.environ", {"PUBCHEM_MAX_RETRIES": "1"}):
            with self.assertRaises(PubChemIntegrationError):
                client.search_by_formula("C9H8O4", limit=1)


if __name__ == "__main__":
    unittest.main()
