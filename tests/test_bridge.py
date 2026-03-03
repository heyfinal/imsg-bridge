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
