import re
import socket

from codearkt.util import get_unique_id, find_free_port, truncate_content, is_correct_json


DOCUMENT = """First line
Second line here
Third line is the target
Fourth line appears
Last line of text"""


def test_get_unique_id_length_and_uniqueness() -> None:
    ids = [get_unique_id() for _ in range(1000)]
    assert all(len(uid) == 8 for uid in ids)
    hex_regex = re.compile(r"^[0-9a-f]{8}$", re.IGNORECASE)
    assert all(hex_regex.match(uid) for uid in ids)
    assert len(ids) == len(set(ids))


def test_find_free_port() -> None:
    port = find_free_port()
    assert port is not None, "No free port returned"
    assert 5000 <= port <= 6000, "Port outside expected range"

    with socket.socket() as s:
        s.bind(("", port))


def test_truncate_content_short() -> None:
    s = "abc"
    assert truncate_content(s, max_length=10) == "abc"


def test_truncate_content_long() -> None:
    s = "a" * 50
    out = truncate_content(s, max_length=10)
    assert len(out) > 10
    assert out.startswith("aaaaa") and out.endswith("aaaaa")
    assert "truncated" in out


def test_is_correct_json() -> None:
    assert is_correct_json('{"a":1}')
    assert not is_correct_json("not json")


def test_truncate_content_centered() -> None:
    result = truncate_content(DOCUMENT, max_length=40, target_line=2)
    lines = DOCUMENT.splitlines()
    parts = result.split("\n\n")
    assert 39 <= len(parts[2]) <= 41
    assert parts[2] in DOCUMENT
    assert lines[2] in result


def test_truncate_content_not_applied() -> None:
    result = truncate_content(DOCUMENT, max_length=500)
    assert result == DOCUMENT


def test_truncate_content_prefix() -> None:
    result = truncate_content(DOCUMENT, max_length=40, prefix_only=True)
    parts = result.split("\n\n")
    assert DOCUMENT.startswith(parts[0])
    assert len(parts[0]) == 40


def test_truncate_content_suffix() -> None:
    result = truncate_content(DOCUMENT, max_length=40, suffix_only=True)
    parts = result.split("\n\n")
    assert DOCUMENT.endswith(parts[2])
    assert len(parts[2]) == 40
