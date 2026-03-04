from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import imsg_bridge.bridge as bridge

AUTH_HEADER = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("IMSG_BRIDGE_TOKEN", "test-token")
    bridge.get_bearer_token.cache_clear()
    bridge.get_imsg_version.cache_clear()

    async def _noop() -> None:
        return None

    monkeypatch.setattr(bridge.manager, "start", _noop)
    monkeypatch.setattr(bridge.manager, "stop", _noop)

    with TestClient(bridge.app) as test_client:
        yield test_client


def test_history_single_object_is_normalized_to_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run_imsg(*_args, **_kwargs):
        return {"id": 1, "guid": "g-1", "chat_id": 23, "text": "hello"}

    monkeypatch.setattr(bridge, "run_imsg", fake_run_imsg)

    response = client.get("/history/23?limit=50", headers=AUTH_HEADER)
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 1,
            "guid": "g-1",
            "chat_id": 23,
            "text": "hello",
            "sender": None,
            "destination_caller_id": None,
            "is_from_me": False,
            "created_at": "",
            "attachments": [],
            "reactions": [],
        }
    ]


def test_history_limit_is_applied(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_imsg(*_args, **_kwargs):
        return [
            {"id": 1, "guid": "g-1", "chat_id": 23},
            {"id": 2, "guid": "g-2", "chat_id": 23},
            {"id": 3, "guid": "g-3", "chat_id": 23},
        ]

    monkeypatch.setattr(bridge, "run_imsg", fake_run_imsg)

    response = client.get("/history/23?limit=2", headers=AUTH_HEADER)
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert [item["id"] for item in response.json()] == [2, 3]


def test_history_descending_input_is_returned_chronological(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run_imsg(*_args, **_kwargs):
        # Simulate imsg returning newest-first rows.
        return [
            {"id": 200, "guid": "g-200", "chat_id": 23},
            {"id": 199, "guid": "g-199", "chat_id": 23},
            {"id": 198, "guid": "g-198", "chat_id": 23},
        ]

    monkeypatch.setattr(bridge, "run_imsg", fake_run_imsg)

    response = client.get("/history/23?limit=2", headers=AUTH_HEADER)
    assert response.status_code == 200
    # Chronological and latest two messages.
    assert [item["id"] for item in response.json()] == [199, 200]


def test_load_state_recovers_from_corrupt_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text("{not valid json")

    monkeypatch.setattr(bridge, "STATE_FILE", state_file)
    monkeypatch.setattr(bridge, "STATE_DIR", tmp_path)

    result = bridge.load_state()

    assert result == {}
    assert state_file.with_suffix(".bad").exists()
    assert not state_file.exists()


def test_health_uses_detected_imsg_version(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_imsg(*_args, **_kwargs):
        return []

    monkeypatch.setattr(bridge, "run_imsg", fake_run_imsg)
    monkeypatch.setattr(bridge, "get_imsg_version", lambda: "0.6.1")

    response = client.get("/health", headers=AUTH_HEADER)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "imsg_version": "0.6.1"}


def test_health_reports_version_even_when_probe_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_run_imsg(*_args, **_kwargs):
        raise HTTPException(status_code=502, detail="imsg error")

    monkeypatch.setattr(bridge, "run_imsg", failing_run_imsg)
    monkeypatch.setattr(bridge, "get_imsg_version", lambda: "0.6.1")

    response = client.get("/health", headers=AUTH_HEADER)
    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "detail": "imsg probe failed",
        "imsg_version": "0.6.1",
    }


def test_phone_lookup_keys_normalize_and_add_fallbacks() -> None:
    keys = bridge._phone_lookup_keys("+1 (405) 315-1310")
    assert "phone:14053151310" in keys
    assert "phone:4053151310" in keys
    assert "phone7:3151310" in keys
    assert "phone4:1310" in keys


def test_decode_contact_image_blob_external_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_name = "ABCD1234-1111-2222-3333-ABCDEFABCDEF"
    image_bytes = b"\xff\xd8\xff\xdbfakejpg"
    (tmp_path / image_name).write_bytes(image_bytes)

    monkeypatch.setattr(bridge, "ADDRESSBOOK_EXTERNAL_DATA", tmp_path)
    pointer_blob = b"\x02" + image_name.encode() + b"\x00"

    decoded = bridge._decode_contact_image_blob(pointer_blob)
    assert decoded == image_bytes


# --- New feature tests ---


def test_ping_does_not_require_auth(client: TestClient) -> None:
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ping_ignores_auth_header(client: TestClient) -> None:
    response = client.get("/ping", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 200


def test_configurable_imsg_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    import imsg_bridge.bridge as b

    monkeypatch.setenv("IMSG_PATH", "/custom/path/imsg")
    importlib.reload(b)
    assert b.IMSG == "/custom/path/imsg"

    monkeypatch.delenv("IMSG_PATH", raising=False)
    importlib.reload(b)
    # Falls back to shutil.which or hardcoded default
    assert b.IMSG is not None


def test_format_display_name() -> None:
    assert bridge._format_display_name("John", "Doe", "Acme") == "John Doe"
    assert bridge._format_display_name("John", None, None) == "John"
    assert bridge._format_display_name(None, "Doe", None) == "Doe"
    assert bridge._format_display_name(None, None, "Acme") == "Acme"
    assert bridge._format_display_name(None, None, None) == ""


def test_contact_name_returns_404_for_unknown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bridge, "_contact_name_index", lambda: {})
    response = client.get("/contact-name?identifier=%2B15550000000", headers=AUTH_HEADER)
    assert response.status_code == 404


def test_contact_name_resolves_known_contact(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        bridge,
        "_contact_name_index",
        lambda: {"phone:15551234567": "Jane Smith"},
    )
    response = client.get("/contact-name?identifier=%2B15551234567", headers=AUTH_HEADER)
    assert response.status_code == 200
    assert response.json()["name"] == "Jane Smith"


def test_rate_limiter_allows_within_limit() -> None:
    import asyncio

    async def _run():
        limiter = bridge.SlidingWindowLimiter(max_requests=3, window_seconds=60.0)
        for _ in range(3):
            allowed, _, _ = await limiter.check()
            assert allowed is True
        allowed, _, retry_after = await limiter.check()
        assert allowed is False
        assert retry_after > 0

    asyncio.run(_run())


def test_send_rate_limited(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_imsg(*_args, **_kwargs):
        return {"status": "sent"}

    monkeypatch.setattr(bridge, "run_imsg", fake_run_imsg)

    # Reset the limiter for this test
    bridge.send_limiter._timestamps.clear()

    for i in range(20):
        response = client.post(
            "/send",
            json={"to": "+15551234567", "text": f"msg {i}"},
            headers=AUTH_HEADER,
        )
        assert response.status_code == 200, f"Request {i} should succeed"

    response = client.post(
        "/send",
        json={"to": "+15551234567", "text": "too many"},
        headers=AUTH_HEADER,
    )
    assert response.status_code == 429
    assert "Retry-After" in response.headers


def test_avatar_cache_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    call_count = 0

    def fake_build():
        nonlocal call_count
        call_count += 1
        return {"phone:1234": b"img"}, {"phone:1234": "Test"}

    monkeypatch.setattr(bridge, "_build_contact_index", fake_build)
    monkeypatch.setattr(bridge, "_avatar_cache_time", 0.0)
    monkeypatch.setattr(bridge, "_name_cache_time", 0.0)

    bridge._contact_avatar_index()
    assert call_count == 1

    # Second call within TTL should not rebuild
    bridge._contact_avatar_index()
    assert call_count == 1

    # Simulate TTL expiry
    monkeypatch.setattr(bridge, "_avatar_cache_time", time.monotonic() - 400)
    bridge._contact_avatar_index()
    assert call_count == 2


def test_send_rejects_file_outside_allowed_dirs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_run_imsg(*_args, **_kwargs):
        return {"status": "sent"}

    monkeypatch.setattr(bridge, "run_imsg", fake_run_imsg)
    bridge.send_limiter._timestamps.clear()

    response = client.post(
        "/send",
        json={"to": "+15551234567", "text": "hi", "file": "/etc/passwd"},
        headers=AUTH_HEADER,
    )
    assert response.status_code in (400, 403)


def test_send_accepts_file_in_allowed_dir(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_run_imsg(*_args, **_kwargs):
        return {"status": "sent"}

    monkeypatch.setattr(bridge, "run_imsg", fake_run_imsg)
    monkeypatch.setattr(bridge, "ALLOWED_ATTACHMENT_DIRS", [tmp_path])
    bridge.send_limiter._timestamps.clear()

    test_file = tmp_path / "photo.jpg"
    test_file.write_bytes(b"\xff\xd8\xff")

    response = client.post(
        "/send",
        json={"to": "+15551234567", "text": "hi", "file": str(test_file)},
        headers=AUTH_HEADER,
    )
    assert response.status_code == 200


def test_send_rejects_nonexistent_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_run_imsg(*_args, **_kwargs):
        return {"status": "sent"}

    monkeypatch.setattr(bridge, "run_imsg", fake_run_imsg)
    monkeypatch.setattr(bridge, "ALLOWED_ATTACHMENT_DIRS", [tmp_path])
    bridge.send_limiter._timestamps.clear()

    response = client.post(
        "/send",
        json={"to": "+15551234567", "text": "hi", "file": str(tmp_path / "nope.jpg")},
        headers=AUTH_HEADER,
    )
    assert response.status_code == 400
