from unittest.mock import Mock, patch

from app.agent import AgentRouter, extract_agent_prompt, extract_backtest_months


def test_agent_fallback_routes_stock_without_treating_words_as_tickers():
    plan = AgentRouter("", "gpt-5-mini").route("Phân tích giúp mình mã FPT")
    assert plan["tool"] == "analyze_stock"
    assert plan["args"]["symbol"] == "FPT"


def test_agent_fallback_does_not_parse_hay_as_ticker():
    assert AgentRouter("", "gpt-5-mini").route("Hãy phân tích mã FPT")["args"]["symbol"] == "FPT"
    assert AgentRouter("", "gpt-5-mini").route("Hãy phân tích mã CTG")["args"]["symbol"] == "CTG"


def test_agent_fallback_routes_comparison():
    plan = AgentRouter("", "gpt-5-mini").route("So sánh FPT với CMG")
    assert plan["tool"] == "compare_stocks"
    assert plan["args"]["symbols"] == ["FPT", "CMG"]


def test_agent_accepts_openai_function_call():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"output": [{"type": "function_call", "name": "market_overview", "arguments": "{}"}]}
    with patch("app.agent.requests.post", return_value=response):
        plan = AgentRouter("key", "gpt-5-mini").route("?")
    assert plan == {"tool": "market_overview", "args": {}, "via": "openai"}


def test_explicit_ticker_overrides_bad_model_argument():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"output": [{"type": "function_call", "name": "analyze_stock",
                                                "arguments": '{"symbol":"HAY"}'}]}
    with patch("app.agent.requests.post", return_value=response):
        plan = AgentRouter("key", "gpt-5-mini").route("Hãy phân tích mã CTG")
    assert plan["args"]["symbol"] == "CTG"


def test_group_messages_require_mention_or_reply():
    assert extract_agent_prompt("mọi người xem FPT", "VNStockAnalysisBot", False) is None
    assert extract_agent_prompt("@VNStockAnalysisBot phân tích FPT", "VNStockAnalysisBot", False) == "phân tích FPT"
    assert extract_agent_prompt("phân tích CTG", "VNStockAnalysisBot", False, replying_to_bot=True) == "phân tích CTG"


def test_local_router_distinguishes_backtest_and_technical_analysis():
    router = AgentRouter("", "gpt-5-mini")
    assert router.route("backtest CTG thử") == {"tool": "backtest_stock", "args": {"symbol": "CTG", "lookback_months": None}, "via": "rules"}
    assert router.route("phân tích kỹ thuật FPT") == {"tool": "technical_analysis", "args": {"symbol": "FPT"}, "via": "rules"}
    assert router.route("kiểm định ctg") == {"tool": "backtest_stock", "args": {"symbol": "CTG", "lookback_months": None}, "via": "rules"}


def test_backtest_period_is_preserved_by_router():
    plan = AgentRouter("", "gpt-5-mini").route("backtest VIC trong 2 năm gần đây")
    assert plan["args"] == {"symbol": "VIC", "lookback_months": 24}
    assert extract_backtest_months("kiểm định FPT 18 tháng") == 18
    assert extract_backtest_months("backtest FPT 3 tháng") == 3
    assert extract_backtest_months("backtest FPT 6m") == 6
    assert AgentRouter("", "gpt-5-mini").route("backtest FPT trong 3 tháng gần đây")["args"]["lookback_months"] == 3


def test_screen_defaults_to_vn100_and_can_use_watchlist():
    router = AgentRouter("", "gpt-5-mini")
    assert router.route("lọc cổ phiếu tốt nhất")["args"]["universe"] == "VN100"
    assert router.route("quét watchlist")["args"]["universe"] == "WATCHLIST"


def test_agent_routes_live_price_request():
    plan = AgentRouter("", "gpt-5-mini").route("giá hiện tại FPT")
    assert plan == {"tool": "live_quote", "args": {"symbol": "FPT"}, "via": "rules"}


def test_agent_routes_position_workflow_and_context():
    router = AgentRouter("", "gpt-5-mini")
    add = router.route("Tôi đã mua FPT giá 120 số lượng 1000")
    assert add["tool"] == "position_add"
    assert add["args"] == {"symbol": "FPT", "quantity": 1000, "entry_price": 120.0}
    assert router.route("stop loss hiện tại thế nào", {"last_symbol": "FPT"})["tool"] == "position_review"
    assert router.route("xem danh mục của tôi")["tool"] == "portfolio_status"


def test_agent_routes_fundamental_only_and_methodology():
    router = AgentRouter("", "gpt-5-mini")
    assert router.route("phân tích cơ bản FPT")["tool"] == "fundamental_analysis"
    assert router.route("giải thích phương pháp backtest")["args"]["topic"] == "backtest"
    assert router.route("giải thích chiến lược và phân bổ vốn")["args"]["topic"] == "strategy"


def test_natural_ranking_requests_are_not_mistaken_for_tickers():
    router = AgentRouter("", "gpt-5-mini")
    sector = router.route("Trong nhóm ngành bán lẻ thì cổ phiếu nào đang dẫn đầu")
    assert sector == {"tool": "screen_sector", "args": {"industry": "ban le"}, "via": "rules"}
    best = router.route("Cổ phiếu đang đáng mua nhất")
    assert best == {"tool": "screen_watchlist", "args": {"universe": "VN100"}, "via": "rules"}
    assert router.route("Trong nhóm ngành ngân hàng cổ phiếu nào tốt nhất")["args"]["industry"] == "ngan hang"


def test_daily_signal_and_market_forecast_requests_are_routed():
    router = AgentRouter("", "gpt-5-mini")
    assert router.route("Các cổ phiếu có tín hiệu buy hôm nay")["tool"] == "screen_watchlist"
    assert router.route("Điểm mua bán FPT hôm nay") == {
        "tool": "analyze_stock", "args": {"symbol": "FPT"}, "via": "rules"}
    assert router.route("Dự báo thị trường sắp tới")["tool"] == "market_overview"
