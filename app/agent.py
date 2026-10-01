from __future__ import annotations

import json
import logging
import re
import unicodedata

import requests


log = logging.getLogger(__name__)


def _plain_text(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text.lower())
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    return normalized.replace("đ", "d")


def extract_backtest_months(text: str) -> int | None:
    """Extract an explicit rolling backtest window from Vietnamese/English text."""
    normalized = _plain_text(text)
    year = re.search(r"\b(\d{1,2})\s*(?:nam|year|years|yr|yrs|y)\b", normalized)
    if year:
        return min(max(int(year.group(1)) * 12, 1), 120)
    month = re.search(r"\b(\d{1,3})\s*(?:thang|month|months|mo|m)\b", normalized)
    if month:
        return min(max(int(month.group(1)), 1), 120)
    return None


def extract_agent_prompt(text: str, bot_username: str, is_private: bool, replying_to_bot: bool = False) -> str | None:
    """Return the prompt the agent should handle, or None when a group message is not addressed to it."""
    username = bot_username.lstrip("@").strip()
    mention = re.compile(rf"@{re.escape(username)}\b", re.IGNORECASE) if username else None
    addressed = bool(mention and mention.search(text))
    if not is_private and not addressed and not replying_to_bot:
        return None
    cleaned = mention.sub(" ", text) if mention else text
    return " ".join(cleaned.split()).strip()


TOOLS = [
    {"type": "function", "name": "analyze_stock", "description": "Phân tích toàn diện một mã cổ phiếu Việt Nam.",
     "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "compare_stocks", "description": "So sánh từ hai đến bốn mã cổ phiếu Việt Nam.",
     "parameters": {"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4}}, "required": ["symbols"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "backtest_stock", "description": "Backtest hoặc kiểm định lịch sử chiến lược cho một mã cổ phiếu trong khoảng thời gian người dùng yêu cầu.",
     "parameters": {"type": "object", "properties": {"symbol": {"type": "string"},
         "lookback_months": {"type": ["integer", "null"], "minimum": 1, "maximum": 120}},
         "required": ["symbol", "lookback_months"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "technical_analysis", "description": "Chỉ phân tích kỹ thuật một mã cổ phiếu.",
     "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "fundamental_analysis", "description": "Chỉ phân tích cơ bản và báo cáo tài chính một mã cổ phiếu.",
     "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "live_quote", "description": "Lấy giá và OHLCV gần thời gian thực trong phiên từ SSI cho một mã.",
     "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "market_overview", "description": "Đánh giá xu hướng VNINDEX và trạng thái thị trường.",
     "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "screen_watchlist", "description": "Quét VN100 hoặc danh sách theo dõi và tìm mã đạt bộ lọc.",
     "parameters": {"type": "object", "properties": {"universe": {"type": ["string", "null"]}},
                    "required": ["universe"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "screen_sector", "description": "Xếp hạng cổ phiếu trong một ngành ICB, ví dụ bán lẻ, ngân hàng, bất động sản.",
     "parameters": {"type": "object", "properties": {"industry": {"type": "string"}},
                    "required": ["industry"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "position_add", "description": "Ghi nhận một vị thế cổ phiếu đã mua để theo dõi rủi ro và trailing stop.",
     "parameters": {"type": "object", "properties": {"symbol": {"type": "string"},
         "quantity": {"type": "integer", "minimum": 1}, "entry_price": {"type": "number", "exclusiveMinimum": 0}},
         "required": ["symbol", "quantity", "entry_price"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "position_review", "description": "Kiểm tra lãi lỗ, hard stop, trailing stop và hành động cho một vị thế đang lưu.",
     "parameters": {"type": "object", "properties": {"symbol": {"type": ["string", "null"]}},
         "required": ["symbol"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "position_remove", "description": "Xóa hoặc đóng một vị thế khỏi danh mục theo dõi.",
     "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}},
         "required": ["symbol"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "portfolio_status", "description": "Xem toàn bộ danh mục vị thế đang được theo dõi.",
     "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "explain_method", "description": "Giải thích chiến lược, dữ liệu, phương pháp kỹ thuật, cơ bản, screen, backtest hoặc quản trị rủi ro.",
     "parameters": {"type": "object", "properties": {"topic": {"type": "string", "enum": ["strategy", "technical", "fundamental", "screen", "backtest", "risk", "data", "general"]}},
         "required": ["topic"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "show_help", "description": "Hiển thị hướng dẫn khi yêu cầu chưa rõ hoặc ngoài phạm vi.",
     "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, "strict": True},
]


class AgentRouter:
    """Bounded agent: the model may select a tool but cannot alter its calculations."""

    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    def route(self, message: str, context: dict | None = None) -> dict:
        local_plan = self._fallback(message)
        if local_plan["tool"] == "show_help" and context and context.get("last_symbol"):
            contextual = self._fallback(f"{message} {context['last_symbol']}")
            if contextual["tool"] != "show_help":
                local_plan = contextual
        # Common stock requests are deterministic and do not need a paid model
        # call. OpenAI is reserved for genuinely ambiguous requests.
        if local_plan["tool"] != "show_help":
            return local_plan
        if self.api_key:
            try:
                response = requests.post(
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={
                        "model": self.model,
                        "instructions": ("Chọn đúng một công cụ cho yêu cầu chứng khoán Việt Nam. "
                                         "Liên kết câu hỏi nối tiếp với ngữ cảnh nếu có. Không trả lời kiến thức tài chính trực tiếp. "
                                         "Mã cổ phiếu phải viết hoa. Chỉ chọn công cụ có đủ đối số; nếu thiếu thì chọn show_help."),
                        "input": message + ("\nNgữ cảnh gần nhất: " + json.dumps(context, ensure_ascii=False) if context else ""),
                        "tools": TOOLS,
                        "tool_choice": "required",
                        "parallel_tool_calls": False,
                        "store": False,
                    }, timeout=20,
                )
                response.raise_for_status()
                for item in response.json().get("output", []):
                    if item.get("type") == "function_call" and item.get("name") in {t["name"] for t in TOOLS}:
                        args = json.loads(item.get("arguments") or "{}")
                        explicit = re.findall(r"(?<![A-Za-z0-9])[A-Z]{2,5}(?![A-Za-z0-9])", message)
                        if item["name"] in {"analyze_stock", "backtest_stock", "technical_analysis",
                                            "fundamental_analysis", "live_quote", "position_add",
                                            "position_review", "position_remove"} and explicit:
                            args["symbol"] = explicit[0]
                        if item["name"] == "backtest_stock":
                            explicit_months = extract_backtest_months(message)
                            if explicit_months is not None:
                                args["lookback_months"] = explicit_months
                        elif item["name"] == "compare_stocks" and len(explicit) >= 2:
                            args["symbols"] = explicit[:4]
                        return {"tool": item["name"], "args": args, "via": "openai"}
            except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
                log.warning("Agent routing fell back to local rules: %s", type(exc).__name__)
        return local_plan

    @staticmethod
    def _fallback(message: str) -> dict:
        normalized = _plain_text(message)
        stopwords = {"HAY", "PHAN", "TICH", "GIUP", "MINH", "CHO", "XEM", "DANH", "GIA", "MA", "CO", "PHIEU",
                     "SO", "SANH", "VOI", "VA", "THI", "TRUONG", "HOM", "NAY", "THE", "NAO", "LOC", "QUET",
                     "TOT", "NHAT", "TRONG", "CAC", "WATCHLIST", "DUOI", "GOC", "NHIN", "DAU", "TU",
                     "KIEM", "DINH", "TEST", "LICH", "SU", "KY", "THUAT", "CHI", "BAO", "THU",
                     "MUC", "CUA", "TOI", "GIAI", "THICH", "PHAP", "NHOM", "NGANH", "BAN", "LE",
                     "DANG", "DAN", "NAO", "DAU", "MUA", "HANG", "TOP", "CON", "NEN", "BUY", "SELL"}
        symbols = []
        # Tickers are conventionally typed in uppercase. Prefer those exact
        # tokens so Vietnamese prose such as “Hãy” can never become HAY.
        explicit = re.findall(r"(?<![A-Za-z0-9])[A-Z]{2,5}(?![A-Za-z0-9])", message)
        lowercase_ticker_context = any(x in normalized for x in (
            "phan tich", "backtest", "kiem dinh", "so sanh", "ky thuat", "co ban",
            "gia hien tai", "trong phien", "da mua", "mua vao", "vi the", "stop loss"))
        candidates = explicit
        if not candidates and lowercase_ticker_context:
            candidates = re.findall(r"\b[a-z]{3}\b", normalized)
        for token in candidates:
            token = token.upper()
            if token not in stopwords | {"VNINDEX", "UP", "DOWN", "SIDEWAY"} and token not in symbols:
                symbols.append(token)
        numbers = [float(x.replace(",", "")) for x in re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", message)]
        sector_match = re.search(
            r"(?:nhom\s+)?nganh\s+(.+?)(?:\s+(?:thi|co phieu|ma nao|nao|dang|dan dau|tot nhat|nen mua).*)?$",
            normalized)
        if sector_match and any(x in normalized for x in ("dan dau", "tot nhat", "dang mua", "nen mua", "co phieu nao")):
            industry = sector_match.group(1).strip(" ,.-")
            if industry:
                return {"tool": "screen_sector", "args": {"industry": industry}, "via": "rules"}
        daily_signal_request = any(x in normalized for x in (
            "tin hieu buy", "tin hieu mua", "tin hieu sell", "tin hieu ban",
            "mua hom nay", "ban hom nay", "diem mua", "diem ban"))
        if daily_signal_request:
            if symbols:
                return {"tool": "analyze_stock", "args": {"symbol": symbols[0]}, "via": "rules"}
            return {"tool": "screen_watchlist", "args": {"universe": "VN100"}, "via": "rules"}
        if (not symbols and ("co phieu" in normalized or "ma nao" in normalized)
                and any(x in normalized for x in ("dang mua", "tot nhat", "nen mua", "dan dau"))):
            return {"tool": "screen_watchlist", "args": {"universe": "VN100"}, "via": "rules"}
        if any(x in normalized for x in ("chien luoc", "phan bo von", "ty trong danh muc")):
            return {"tool": "explain_method", "args": {"topic": "strategy"}, "via": "rules"}
        if any(x in normalized for x in ("phuong phap", "cach tinh", "giai thich", "du lieu tu dau", "nguon du lieu")):
            if any(x in normalized for x in ("backtest", "kiem dinh")): topic = "backtest"
            elif any(x in normalized for x in ("screen", "loc", "quet")): topic = "screen"
            elif any(x in normalized for x in ("rui ro", "stop", "trailing")): topic = "risk"
            elif any(x in normalized for x in ("co ban", "bctc")): topic = "fundamental"
            elif any(x in normalized for x in ("ky thuat", "chi bao")): topic = "technical"
            elif "du lieu" in normalized or "nguon" in normalized: topic = "data"
            else: topic = "general"
            return {"tool": "explain_method", "args": {"topic": topic}, "via": "rules"}
        position_words = ("vi the", "danh muc", "trailing", "trailing stop", "stop loss", "cat lo",
                          "gia von", "dang nam", "quan tri rui ro", "bao ve loi nhuan")
        if any(x in normalized for x in ("dong vi the", "xoa vi the", "ban xong", "remove position")) and symbols:
            return {"tool": "position_remove", "args": {"symbol": symbols[0]}, "via": "rules"}
        if any(x in normalized for x in ("them vi the", "ghi nhan", "da mua", "mua vao", "position add")) and symbols and len(numbers) >= 2:
            price_match = re.search(r"(?:gia|gia von)\s*[:=]?\s*(\d[\d,]*(?:\.\d+)?)", normalized)
            qty_match = re.search(r"(?:so luong|sl)\s*[:=]?\s*(\d[\d,]*(?:\.\d+)?)", normalized)
            entry_price = float(price_match.group(1).replace(",", "")) if price_match else max(numbers)
            quantity = int(float(qty_match.group(1).replace(",", ""))) if qty_match else int(min(numbers))
            return {"tool": "position_add", "args": {"symbol": symbols[0],
                    "quantity": quantity, "entry_price": entry_price}, "via": "rules"}
        if any(x in normalized for x in ("danh muc", "cac vi the", "portfolio")) and not symbols:
            return {"tool": "portfolio_status", "args": {}, "via": "rules"}
        if any(x in normalized for x in position_words) and symbols:
            return {"tool": "position_review", "args": {"symbol": symbols[0]}, "via": "rules"}
        if any(x in normalized for x in ("backtest", "kiem dinh", "test lich su")) and symbols:
            return {"tool": "backtest_stock", "args": {
                "symbol": symbols[0], "lookback_months": extract_backtest_months(message)}, "via": "rules"}
        if any(x in normalized for x in ("realtime", "real time", "gia live", "gia hien tai", "trong phien")) and symbols:
            return {"tool": "live_quote", "args": {"symbol": symbols[0]}, "via": "rules"}
        if any(x in normalized for x in ("co ban", "fundamental", "bao cao tai chinh", "bctc")) and symbols:
            return {"tool": "fundamental_analysis", "args": {"symbol": symbols[0]}, "via": "rules"}
        if any(x in normalized for x in ("ky thuat", "technical", "ta only", "chi bao")) and symbols:
            return {"tool": "technical_analysis", "args": {"symbol": symbols[0]}, "via": "rules"}
        if any(x in normalized for x in ("thi truong", "vnindex", "xu huong chung", "du bao sap toi")):
            return {"tool": "market_overview", "args": {}, "via": "rules"}
        if any(x in normalized for x in ("loc", "quet", "watchlist", "tot nhat")):
            universe = "WATCHLIST" if "watchlist" in normalized or "danh sach theo doi" in normalized else "VN100"
            if "vn30" in normalized:
                universe = "VN30"
            elif "vn100" in normalized:
                universe = "VN100"
            return {"tool": "screen_watchlist", "args": {"universe": universe}, "via": "rules"}
        if any(x in normalized for x in ("so sanh", "voi", "va")) and len(symbols) >= 2:
            return {"tool": "compare_stocks", "args": {"symbols": symbols[:4]}, "via": "rules"}
        if symbols:
            return {"tool": "analyze_stock", "args": {"symbol": symbols[0]}, "via": "rules"}
        return {"tool": "show_help", "args": {}, "via": "rules"}
