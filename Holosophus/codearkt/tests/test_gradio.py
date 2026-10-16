from codearkt.gradio import _resolve_server_address


def test_resolve_server_address_from_url() -> None:
    host, port = _resolve_server_address(
        host="0.0.0.0",
        port=5055,
        url="http://127.0.0.1:5055",
    )

    assert host == "127.0.0.1"
    assert port == 5055

