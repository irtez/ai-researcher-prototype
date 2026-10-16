"""Offline client/retry regressions; run directly with Python in the academia image."""

import importlib
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import requests
from huggingface_hub import HfApi

hf = importlib.import_module("academia_mcp.tools.hf_datasets_search")
s2 = importlib.import_module("academia_mcp.tools.s2")
s2_client = importlib.import_module("academia_mcp.s2_client")
from academia_mcp.utils import get_with_retries


class DatasetSearchTests(unittest.TestCase):
    def test_supported_sort_names_reach_the_client_without_direction(self):
        for public_name, api_name in (
            ("last_modified", "lastModified"),
            ("created_at", "createdAt"),
            ("trending_score", "trendingScore"),
            ("downloads", "downloads"),
            ("likes", "likes"),
        ):
            with (
                self.subTest(sort=public_name),
                patch(
                    "huggingface_hub.hf_api.paginate", return_value=iter([])
                ) as paginate,
            ):
                result = hf.hf_datasets_search(sort_by=public_name)
                self.assertEqual([], result.results)
                self.assertEqual(
                    {"sort": api_name, "limit": 5}, paginate.call_args.kwargs["params"]
                )

    def test_current_client_sends_exact_sort_filter_and_bounded_limit(self):
        calls = []

        def paginate(path, params, headers):
            calls.append((path, params))
            return iter(
                [
                    {
                        "id": f"example/data-{i}",
                        "downloads": 20 - i,
                        "likes": 2,
                        "tags": ["language:ru"],
                    }
                    for i in range(12)
                ]
            )

        with (
            patch.object(
                hf, "HF_API", HfApi(endpoint="http://offline.invalid", token=False)
            ),
            patch("huggingface_hub.hf_api.paginate", side_effect=paginate),
            patch.object(hf, "hf_hub_download", side_effect=FileNotFoundError),
        ):
            result = hf.hf_datasets_search(
                query="parabens",
                search_filter=["language:ru"],
                sort_by="last_modified",
                limit=100,
            )
        self.assertEqual(10, len(result.results))
        self.assertEqual(
            [f"example/data-{i}" for i in range(10)],
            [entry.id for entry in result.results],
        )
        self.assertEqual(
            [
                (
                    "http://offline.invalid/api/datasets",
                    {
                        "search": ["parabens"],
                        "filter": ["language:ru"],
                        "sort": "lastModified",
                        "limit": 10,
                    },
                )
            ],
            calls,
        )

    def test_optional_metadata_and_readme_can_be_absent(self):
        with (
            patch(
                "huggingface_hub.hf_api.paginate",
                return_value=iter([{"id": "example/bare"}]),
            ),
            patch.object(hf, "hf_hub_download", side_effect=FileNotFoundError),
        ):
            result = hf.hf_datasets_search()
        entry = result.results[0]
        self.assertEqual(
            ("", "", 0, 0, [], ""),
            (
                entry.created_at,
                entry.last_modified,
                entry.downloads,
                entry.likes,
                entry.tags,
                entry.readme,
            ),
        )

    def test_ascending_is_explicitly_rejected_before_a_request(self):
        with patch(
            "huggingface_hub.hf_api.paginate",
            side_effect=AssertionError("network called"),
        ):
            with self.assertRaisesRegex(ValueError, "descending"):
                hf.hf_datasets_search(sort_order="ascending")

    def test_invalid_sort_and_nonpositive_limit_are_rejected_before_a_request(self):
        for arguments in (
            {"sort_by": "invented"},
            {"sort_order": "sideways"},
            {"limit": 0},
            {"limit": -1},
        ):
            with (
                self.subTest(arguments=arguments),
                patch(
                    "huggingface_hub.hf_api.paginate",
                    side_effect=AssertionError("network called"),
                ),
            ):
                with self.assertRaises(ValueError):
                    hf.hf_datasets_search(**arguments)


class SemanticScholarSearchTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.statuses = [429]
        self.retry_after = None
        self.headers = []
        self.now = 1000.0
        self.waits = []
        self.latency = 0.0

        def sleep(seconds):
            self.waits.append(seconds)
            self.now += seconds

        for item in (
            patch.object(s2_client, "monotonic", lambda: self.now),
            patch.object(s2_client, "sleep", sleep),
            patch.object(s2_client, "time", lambda: 1700000000.0),
            patch.object(s2_client, "_next_key_request", 0.0),
            patch.object(s2_client, "_cooldown_until", 0.0),
            patch.object(s2.settings, "S2_API_KEY", None),
        ):
            item.start()
            self.addCleanup(item.stop)
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fixture.requests.append(urlparse(self.path))
                fixture.headers.append(dict(self.headers))
                fixture.now += fixture.latency
                status = fixture.statuses[
                    min(len(fixture.requests) - 1, len(fixture.statuses) - 1)
                ]
                body = (
                    {"error": "Too Many Requests"}
                    if status == 429
                    else {
                        "total": 1,
                        "offset": 2,
                        "data": [
                            {
                                "paperId": "fixture",
                                "title": "Fixture paper",
                                "authors": [{"name": "J. Doe"}],
                                "citationCount": 42,
                                "venue": "Test Journal",
                                "publicationDate": "2026-01-01",
                            }
                        ],
                    }
                )
                if status == 200 and self.path.startswith("/paper/arxiv:"):
                    body = body["data"][0]
                encoded = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                if fixture.retry_after is not None:
                    self.send_header("Retry-After", fixture.retry_after)
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = patch.object(
            s2, "BASE_URL", f"http://127.0.0.1:{self.server.server_port}"
        )
        self.base.start()
        self.addCleanup(self.base.stop)
        self.proxy = patch.object(s2.settings, "S2_PROXY_ENABLED", False)
        self.proxy.start()
        self.addCleanup(self.proxy.stop)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_repeated_429_uses_spaced_retries(self):
        with self.assertRaisesRegex(RuntimeError, "429"):
            s2.s2_search("parabens")
        self.assertEqual(4, len(self.requests))
        self.assertEqual([2, 4, 8], self.waits)

    def test_retry_after_beyond_budget_does_not_sleep_or_retry_early(self):
        for retry_after in ("3600", "Fri, 09 Oct 2099 00:00:00 GMT"):
            with (
                self.subTest(retry_after=retry_after),
                patch.object(s2_client, "_cooldown_until", 0),
            ):
                self.retry_after = retry_after
                self.requests.clear()
                self.waits.clear()
                with self.assertRaisesRegex(RuntimeError, "429"):
                    s2.s2_search("parabens")
                # A subsequent call must respect the same server cooldown as well.
                with self.assertRaises(RuntimeError):
                    s2.s2_search("another query")
                self.assertEqual(1, len(self.requests))
                self.assertEqual([], self.waits)

    def test_retry_configuration_is_honored_with_a_safe_runtime_cap(self):
        for configured, expected_requests in (
            (-50, 1),
            (0, 1),
            (1, 2),
            (3, 4),
            (1000, 4),
        ):
            self.requests.clear()
            with (
                self.subTest(configured=configured),
                patch.object(s2.settings, "S2_MAX_RETRIES", configured),
            ):
                with self.assertRaises(Exception):
                    s2.s2_search("parabens")
                self.assertEqual(expected_requests, len(self.requests))

    def test_retry_after_seconds_date_and_invalid_value(self):
        from email.utils import formatdate

        for header, expected in [
            ("5", 5),
            (formatdate(1700000007, usegmt=True), 7),
            ("invalid", 2),
        ]:
            with (
                self.subTest(header=header),
                patch.object(s2_client, "_cooldown_until", 0),
            ):
                self.retry_after = header
                self.statuses = [429, 200]
                self.requests.clear()
                self.waits.clear()
                result = s2.s2_search("parabens")
                self.assertEqual(1, result.returned_count)
                self.assertEqual([expected], self.waits)

    def test_elapsed_requests_count_towards_the_budget(self):
        self.latency = 13.0
        with self.assertRaisesRegex(RuntimeError, "429"):
            s2.s2_search("parabens")
        self.assertEqual(2, len(self.requests))
        self.assertEqual([2], self.waits)
        self.assertLessEqual(self.now, 1030)

    def test_optional_key_is_sent_only_to_s2_and_key_requests_are_paced(self):
        self.statuses = [200]
        with patch.object(s2.settings, "S2_API_KEY", "fixture-key"):
            s2.s2_search("parabens")
            self.assertEqual("Fixture paper", s2.s2_get_info("1234.5678").title)
        self.assertEqual([1], self.waits)
        for headers in self.headers:
            self.assertEqual("fixture-key", headers.get("x-api-key"))
            self.assertNotIn("Authorization", headers)

    def test_no_key_and_nonretryable_status_do_not_retry(self):
        self.statuses = [403]
        with self.assertRaisesRegex(RuntimeError, "403"):
            s2.s2_search("parabens")
        self.assertEqual(1, len(self.requests))
        self.assertNotIn("x-api-key", self.headers[0])
        self.assertEqual([], self.waits)

    def test_transient_rate_limit_preserves_query_and_successful_results(self):
        self.statuses = [429, 200]
        with patch("urllib3.util.retry.time.sleep"):
            result = s2.s2_search(
                "metal & solubility",
                offset=2,
                limit=7,
                min_citation_count=5,
                publication_date="2020:2026",
            )
        self.assertEqual(
            (1, 1, 2, "Fixture paper"),
            (
                result.total_count,
                result.returned_count,
                result.offset,
                result.results[0].title,
            ),
        )
        self.assertEqual(2, len(self.requests))
        self.assertEqual("/paper/search", self.requests[0].path)
        self.assertEqual(
            {
                "query": ["metal & solubility"],
                "offset": ["2"],
                "limit": ["7"],
                "minCitationCount": ["5"],
                "publicationDateOrYear": ["2020:2026"],
                "fields": [s2.FIELDS],
            },
            parse_qs(self.requests[0].query),
        )

    def test_network_timeout_has_short_connect_and_read_limits(self):
        with patch(
            "requests.Session.get", side_effect=requests.Timeout("fixture timeout")
        ) as get:
            with self.assertRaisesRegex(RuntimeError, "web_search"):
                s2.s2_search("parabens")
        timeout = get.call_args.kwargs["timeout"]
        self.assertLessEqual(timeout.connect_timeout, 3)
        self.assertLessEqual(timeout.read_timeout, 5)

    def test_shared_get_helper_keeps_existing_retry_after_defaults(self):
        self.retry_after = "2"
        with patch("urllib3.util.retry.time.sleep") as sleep:
            with self.assertRaises(requests.exceptions.RetryError):
                get_with_retries(
                    f"http://127.0.0.1:{self.server.server_port}/other-tool",
                    num_retries=1,
                )
        self.assertEqual(2, len(self.requests))
        self.assertEqual([2], [call.args[0] for call in sleep.call_args_list])


if __name__ == "__main__":
    unittest.main()
