# -*- coding: utf-8 -*-
"""core/tts_client.py 测试：用 httpx MockTransport 模拟 Fish 服务。"""

from __future__ import annotations

from pathlib import Path

import httpx

from . import loader, tmpdir

MOD = loader.import_core(Path(__file__).resolve().parents[1], "tts_client")


def _make_client(handler):
    """构造一个把请求交给 handler 的 httpx 客户端。"""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


async def test_synthesize_success():
    d = tmpdir.make_tmpdir("tts")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer abc123"
        assert request.headers["model"] == "s2.1-pro"
        body = request.read().decode("utf-8")
        assert "format" in body and "mp3" in body
        return httpx.Response(200, content=b"FAKE_MP3", headers={"content-type": "audio/mpeg"})

    client = _make_client(handler)
    try:
        tts = MOD.FishTTS(d, client=client)
        cfg = MOD.FishConfig(api_key="abc123")
        path = await tts.synthesize("你好", cfg)
        assert path.exists()
        assert path.read_bytes() == b"FAKE_MP3"
        assert path.suffix == ".mp3"
    finally:
        await client.aclose()
        tmpdir.rm_tmpdir(d)


async def test_no_api_key_raises():
    d = tmpdir.make_tmpdir("ttsk")
    try:
        tts = MOD.FishTTS(d, client=_make_client(lambda r: httpx.Response(200)))
        cfg = MOD.FishConfig(api_key="")
        try:
            await tts.synthesize("你好", cfg)
            assert False, "应当抛 FishError"
        except MOD.FishError as e:
            assert "API Key" in str(e)
    finally:
        tmpdir.rm_tmpdir(d)


async def test_retry_then_success():
    d = tmpdir.make_tmpdir("ttsr")
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, content=b"busy")
        return httpx.Response(200, content=b"OK", headers={"content-type": "audio/mpeg"})

    client = _make_client(handler)
    try:
        tts = MOD.FishTTS(d, client=client)
        cfg = MOD.FishConfig(api_key="k", retries=3, timeout=30)
        path = await tts.synthesize("hi", cfg)
        assert path.read_bytes() == b"OK"
        assert calls["n"] == 3
    finally:
        await client.aclose()
        tmpdir.rm_tmpdir(d)


async def test_auth_error_no_retry():
    d = tmpdir.make_tmpdir("ttsa")
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        return httpx.Response(401, content=b"invalid key")

    client = _make_client(handler)
    try:
        tts = MOD.FishTTS(d, client=client)
        cfg = MOD.FishConfig(api_key="bad", retries=3)
        try:
            await tts.synthesize("hi", cfg)
            assert False, "应当抛 FishError"
        except MOD.FishError as e:
            assert e.status == 401
        assert calls["n"] == 1  # 401 不重试
    finally:
        await client.aclose()
        tmpdir.rm_tmpdir(d)


async def test_reverse_proxy_html_error_page():
    d = tmpdir.make_tmpdir("ttsh")

    async def handler(request):
        return httpx.Response(200, content=b"<html>login page</html>", headers={"content-type": "text/html"})

    client = _make_client(handler)
    try:
        tts = MOD.FishTTS(d, client=client)
        cfg = MOD.FishConfig(api_key="k", retries=0)
        try:
            await tts.synthesize("hi", cfg)
            assert False, "应当抛 FishError"
        except MOD.FishError:
            pass
    finally:
        await client.aclose()
        tmpdir.rm_tmpdir(d)


async def test_cleanup_old_files():
    import time
    d = tmpdir.make_tmpdir("ttsc")
    old = d / "fishaudio_0000000000000_aaaaaaaaaaaa.mp3"
    old.write_bytes(b"x")
    old_time = time.time() - 7200
    import os
    os.utime(old, (old_time, old_time))
    fresh = d / "fishaudio_0000000000001_bbbbbbbbbbbb.mp3"
    fresh.write_bytes(b"y")

    tts = MOD.FishTTS(d)
    tts._last_cleanup = -1000.0  # 让清理立刻执行
    tts._cleanup()
    assert not old.exists()  # 超龄文件被清掉
    assert fresh.exists()


def run():
    import asyncio
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            asyncio.run(fn())
    print("test_core_tts_client: 全部通过")


if __name__ == "__main__":
    run()
