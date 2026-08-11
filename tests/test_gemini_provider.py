from __future__ import annotations

from src.daily_stock_analyse.ai_providers.gemini_provider import GeminiOverlayProvider


def test_gemini_generate_overlay_builds_expected_request(monkeypatch):
    captured: dict = {}

    class _FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": '{"summary":"ok","action_points":["a1","a2","a3"]}'
                                }
                            ]
                        }
                    }
                ]
            }

    def _fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _FakeResponse()

    monkeypatch.setattr("src.daily_stock_analyse.ai_providers.gemini_provider.requests.post", _fake_post)

    provider = GeminiOverlayProvider("gemini-test-key")
    result = provider.generate_overlay({"symbol": "AMD"})

    assert result.provider == "gemini"
    assert result.summary == "ok"
    assert result.action_points == ["a1", "a2", "a3"]

    assert (
        captured["url"]
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"
    )
    assert captured["params"] == {"key": "gemini-test-key"}
    assert captured["timeout"] == 30

    body = captured["json"]
    assert body["contents"][0]["role"] == "user"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert "Payload:\n" in body["contents"][0]["parts"][0]["text"]
