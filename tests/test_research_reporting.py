from datetime import date
from unittest.mock import Mock, patch

from app.fundamental import FundamentalEngine
from app.research import WebResearchService


def test_web_research_preserves_citation_url():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"output": [
        {"type": "web_search_call", "action": {"sources": [{"title": "HOSE", "url": "https://hsx.vn/example"}]}},
        {"type": "message", "content": [{"type": "output_text", "text": "Sự kiện đã công bố.", "annotations": []}]},
    ]}
    with patch("app.research.requests.post", return_value=response):
        result = WebResearchService("key", "gpt-5-mini").company_brief("FPT")
    assert result.status == "OK"
    assert result.sources[0]["url"] == "https://hsx.vn/example"


def test_free_fundamental_uses_sector_policy_weights():
    snapshot = {"schema": "vnstock_free_ratio", "as_of": date.today(), "company_type": "CT",
                "source": "test", "metrics": {"industry": "Bất động sản", "eps_cagr": .12,
                "roe": .15, "roic": .10, "ebit_margin": .12, "debt_to_equity": .8,
                "current_ratio": 1.3, "pe_ratio": 14, "pb_ratio": 1.8, "ev_to_ebitda": 10}}
    result = FundamentalEngine().analyze("VHM", snapshot)
    assert result.score is not None
    assert "health 35%" in result.metrics["model_weights"]
