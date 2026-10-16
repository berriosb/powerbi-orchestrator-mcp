from __future__ import annotations

from typing import Any

import pytest

from powerbi_orchestrator_mcp.cloud.auth import READ_SCOPES, WRITE_SCOPES
from powerbi_orchestrator_mcp.cloud.fabric_client import (
    FabricClient,
    _summarize_response_body,
)


class _Recorder:
    def __init__(self) -> None:
        self.scopes: list[Any] = []


class _Cred:
    def __init__(self) -> None:
        self.recorder = _Recorder()

    async def get_token(self, scopes: Any = WRITE_SCOPES) -> str:
        self.recorder.scopes.append(scopes)
        return "tok"


def _client(cred: _Cred) -> FabricClient:
    client = FabricClient(cred)  # type: ignore[arg-type]
    fake = _Http(_Resp())
    client._fabric_client = fake  # type: ignore[assignment]
    client._pbi_client = fake  # type: ignore[assignment]
    return client


class _Resp:
    def __init__(self, status: int = 200, text: str = '{"ok":1}', content: bytes = b"{}") -> None:
        self.status_code = status
        self.text = text
        self.content = content

    def json(self) -> dict[str, Any]:
        return {"ok": 1}


class _Http:
    def __init__(self, resp: _Resp) -> None:
        self.resp = resp

    async def request(self, *_a: Any, **_kw: Any) -> _Resp:
        return self.resp

    async def aclose(self) -> None:
        return None


class TestScopeSeparation:
    @pytest.mark.asyncio
    async def test_get_uses_read_scopes(self) -> None:
        cred = _Cred()
        client = _client(cred)

        await client.get("/groups/x")

        assert cred.recorder.scopes == [READ_SCOPES]

    @pytest.mark.asyncio
    async def test_post_uses_write_scopes(self) -> None:
        cred = _Cred()
        client = _client(cred)

        await client.post("/groups/x/items", json={"a": 1})

        assert cred.recorder.scopes == [WRITE_SCOPES]

    @pytest.mark.asyncio
    async def test_patch_uses_write_scopes(self) -> None:
        cred = _Cred()
        client = _client(cred)

        await client.patch("/groups/x", json={"a": 1})

        assert cred.recorder.scopes == [WRITE_SCOPES]

    def test_read_and_write_scopes_differ(self) -> None:
        assert set(READ_SCOPES) < set(WRITE_SCOPES)
        assert set(READ_SCOPES) != set(WRITE_SCOPES)


class TestErrorBodySummarisation:
    def test_empty_body(self) -> None:
        assert _summarize_response_body("") == "<empty body>"

    def test_short_body_is_kept(self) -> None:
        assert _summarize_response_body('{"error":"nope"}') == '{"error":"nope"}'

    def test_long_body_is_truncated(self) -> None:
        body = "x" * 5000

        result = _summarize_response_body(body)

        assert result.endswith("truncated, 5000 chars>")
        assert len(result) < 500

    def test_whitespace_is_collapsed(self) -> None:
        assert _summarize_response_body("a\n\n   b") == "a b"


class TestErrorsDoNotLeakBodies:
    @pytest.mark.asyncio
    async def test_client_error_body_is_bounded(self) -> None:
        from powerbi_orchestrator_mcp.cloud.fabric_client import FabricAPIError

        cred = _Cred()
        client = _client(cred)
        fake = _Http(_Resp(400, "secret " * 2000))
        client._fabric_client = fake  # type: ignore[assignment]
        client._pbi_client = fake  # type: ignore[assignment]

        with pytest.raises(FabricAPIError) as excinfo:
            await client.get("/groups/x")

        message = str(excinfo.value)
        assert "truncated" in message
        assert len(message) < 500
        assert (excinfo.value.response_body or "").count("secret") < 2000

    @pytest.mark.asyncio
    async def test_server_error_body_is_bounded(self) -> None:
        from powerbi_orchestrator_mcp.cloud.fabric_client import FabricAPIError

        cred = _Cred()
        client = _client(cred)
        fake = _Http(_Resp(500, "y" * 4000))
        client._fabric_client = fake  # type: ignore[assignment]
        client._pbi_client = fake  # type: ignore[assignment]

        with pytest.raises(FabricAPIError) as excinfo:
            await client.post("/groups/x/items", json={})

        assert "truncated" in str(excinfo.value)
