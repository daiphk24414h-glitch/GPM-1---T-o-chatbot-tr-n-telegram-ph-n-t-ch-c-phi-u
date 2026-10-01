from unittest.mock import Mock, patch

import requests

from app.ai import AIExplainer


def test_openai_success_uses_responses_output():
    response = Mock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = {"output": [{"content": [{"type": "output_text", "text": "Phân tích hợp lệ"}]}]}
    with patch("app.ai.requests.post", return_value=response):
        result = AIExplainer(openai_api_key="configured-key").explain({"score": 61})
    assert result == "Phân tích hợp lệ"


def test_ai_402_falls_back_without_breaking_analysis():
    response = Mock(status_code=402)
    response.raise_for_status.side_effect = requests.HTTPError("quota")
    with patch("app.ai.requests.post", return_value=response):
        result = AIExplainer(openai_api_key="configured-key").explain({"score": 61})
    assert "Kết luận định lượng" in result


def test_ai_network_error_falls_back_without_breaking_analysis():
    with patch("app.ai.requests.post", side_effect=requests.ConnectionError("offline")):
        result = AIExplainer(openai_api_key="configured-key").explain({"score": 61})
    assert "Kết luận định lượng" in result
