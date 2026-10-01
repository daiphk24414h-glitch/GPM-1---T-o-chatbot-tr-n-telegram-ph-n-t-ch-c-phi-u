import logging
import time

import requests


log = logging.getLogger(__name__)


class AIExplainer:
    def __init__(self, openai_api_key: str = "", openai_model: str = "gpt-5-mini",
                 deepseek_api_key: str = "", deepseek_model: str = "deepseek-chat"):
        self.openai_api_key, self.openai_model = openai_api_key, openai_model
        self.deepseek_api_key, self.deepseek_model = deepseek_api_key, deepseek_model
        self._openai_cooldown_until = 0.0

    def explain(self, payload: dict) -> str:
        if not self.openai_api_key and not self.deepseek_api_key:
            return self._quantitative_fallback(payload)
        prompt = ("Bạn là chuyên gia phân tích tài chính Việt Nam có tư duy phản biện. Dựa riêng trên output "
                  "định lượng được cung cấp, hãy đưa ra tối đa 6 gạch đầu dòng gồm: luận điểm chính, yếu tố xác nhận, "
                  "mâu thuẫn giữa kỹ thuật và cơ bản, rủi ro, điều kiện làm luận điểm mất hiệu lực và nhận định có điều kiện. "
                  "Không thêm số liệu, không đổi tín hiệu, không che giấu dữ liệu thiếu, không khẳng định chắc chắn và "
                  "không gọi đây là lời khuyên đầu tư. Dữ liệu: " + repr(payload))
        if self.openai_api_key:
            result = self._openai(prompt)
            if result:
                return result
        if self.deepseek_api_key:
            result = self._deepseek(prompt)
            if result:
                return result
        return self._quantitative_fallback(payload)

    @staticmethod
    def _quantitative_fallback(payload: dict) -> str:
        """Useful deterministic commentary when an external language model is unavailable."""
        task = payload.get("task")
        if task == "backtest":
            cagr = float(payload.get("cagr_pct", 0))
            mdd = float(payload.get("max_drawdown_pct", 0))
            sharpe = float(payload.get("sharpe", 0))
            win = float(payload.get("win_rate_pct", 0))
            pf = payload.get("profit_factor", 0)
            trades = int(payload.get("trades", 0))
            quality = "tích cực" if cagr > 0 and sharpe >= 1 and mdd <= 20 else "chưa đủ thuyết phục"
            sample = "mẫu giao dịch còn mỏng" if trades < 30 else "số lệnh đã đủ để tham khảo sơ bộ"
            return (f"• Hiệu quả kiểm định {quality}: CAGR {cagr:.2f}% đi cùng MDD {mdd:.2f}% và Sharpe {sharpe:.2f}.\n"
                    f"• Win rate {win:.1f}%, profit factor {pf}; {sample} ({trades} lệnh).\n"
                    "• Kết quả chưa phản ánh bộ lọc cơ bản point-in-time, vì vậy chưa thể dùng để khẳng định hiệu quả chiến lược hợp lưu.\n"
                    "• Luận điểm bị bác bỏ nếu kết quả out-of-sample hoặc walk-forward suy giảm rõ rệt sau phí và trượt giá.")
        if task == "compare":
            companies = payload.get("companies") or []
            ranked = sorted(companies, key=lambda x: x.get("combined") if x.get("combined") is not None else -1,
                            reverse=True)
            if not ranked:
                return "• Chưa có đủ dữ liệu đồng nhất để hình thành so sánh định lượng."
            leader = ranked[0]
            lines = [f"• {leader.get('symbol')} đang dẫn đầu nhóm so sánh với điểm tổng hợp "
                     f"{leader.get('combined'):.1f}/100." if leader.get("combined") is not None else
                     f"• {leader.get('symbol')} đứng đầu phần dữ liệu hiện có nhưng chưa đủ điểm cơ bản để kết luận."]
            for item in ranked:
                lines.append(f"• {item.get('symbol')}: tín hiệu {item.get('signal')}, chiến lược {item.get('strategy')}, "
                             f"TA {item.get('technical_score'):.1f}, FA "
                             f"{'N/A' if item.get('fundamental_score') is None else f'{item.get('fundamental_score'):.1f}' }.")
            lines.append("• Xếp hạng chỉ có ý nghĩa trong nhóm mã được yêu cầu và cần kiểm tra thêm định giá peer cùng ngành.")
            return "\n".join(lines)
        tech = payload.get("technical") or {}
        fund = payload.get("fundamental") or {}
        signal = payload.get("final_signal", tech.get("signal", "HOLD"))
        ts, fs, combined = tech.get("score"), fund.get("score"), payload.get("combined_80_20")
        regime, strategy = tech.get("regime", "UNKNOWN"), tech.get("strategy", "NONE")
        lines = [f"• Kết luận định lượng là {signal}; chiến lược {strategy} trong trạng thái VNINDEX {regime}."]
        if ts is not None:
            lines.append(f"• Điểm kỹ thuật {float(ts):.1f}/100; tín hiệu chỉ được xác nhận khi điều kiện giá và khối lượng của setup tiếp tục duy trì.")
        if fs is None:
            lines.append("• Điểm cơ bản chưa đủ độ phủ, vì vậy không dùng phần này để nâng tín hiệu mua.")
        else:
            lines.append(f"• Điểm cơ bản {float(fs):.1f}/100 và điểm hợp lưu "
                         f"{'N/A' if combined is None else f'{float(combined):.1f}/100'}; cần đọc cùng kỳ báo cáo và độ mới dữ liệu.")
        price, stop, take = tech.get("price"), tech.get("stop_loss"), tech.get("take_profit")
        if all(isinstance(x, (int, float)) for x in (price, stop, take)):
            lines.append(f"• Vùng quản trị rủi ro lấy giá {price:,.2f}, stop-loss {stop:,.2f} và mục tiêu {take:,.2f}; "
                         "đóng cửa vi phạm stop-loss làm luận điểm mất hiệu lực.")
        missing = fund.get("missing") or []
        if missing:
            lines.append("• Độ tin cậy bị giới hạn bởi dữ liệu còn thiếu: " + ", ".join(map(str, missing)) + ".")
        return "\n".join(lines)

    def _openai(self, prompt: str) -> str | None:
        if time.monotonic() < self._openai_cooldown_until:
            return None
        try:
            response = requests.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {self.openai_api_key}", "Content-Type": "application/json"},
                json={"model": self.openai_model, "input": prompt, "store": False}, timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data.get("output_text"), str):
                return data["output_text"].strip() or None
            for item in data.get("output", []):
                for part in item.get("content", []):
                    if part.get("type") == "output_text" and part.get("text"):
                        return part["text"].strip()
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 429:
                self._openai_cooldown_until = time.monotonic() + 900
            log.warning("OpenAI explanation unavailable: %s%s", type(exc).__name__,
                        f"; HTTP {status}" if status else "")
        return None

    def _deepseek(self, prompt: str) -> str | None:
        try:
            response = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.deepseek_api_key}"},
                json={"model": self.deepseek_model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2},
                timeout=20,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip() or None
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            log.warning("DeepSeek explanation unavailable: %s", type(exc).__name__)
            return None
