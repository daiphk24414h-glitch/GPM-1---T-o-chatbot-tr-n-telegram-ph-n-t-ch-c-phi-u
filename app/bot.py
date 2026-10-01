from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from datetime import date
from pathlib import Path

import pandas as pd
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .agent import AgentRouter, extract_agent_prompt, extract_backtest_months
from .ai import AIExplainer
from .backtest import BacktestEngine
from .charts import backtest_chart, price_chart
from .config import Settings
from .data import DataError, MarketDataService
from .database import UserStore
from .market import analyze_market
from .portfolio import PositionRiskEngine
from .reporting import fundamental_report, research_sources, technical_report
from .research import WebResearchService
from .service import AnalysisService
from .ssi import SSIDataClient


log = logging.getLogger(__name__)


class TelegramBot:
    SCREEN_COMPONENTS = ("relative_strength", "trend", "entry_quality", "volume", "risk_liquidity")

    def __init__(self, settings: Settings):
        self.settings = settings
        self.ssi = SSIDataClient(settings.ssi_consumer_id, settings.ssi_consumer_secret,
                                 settings.ssi_data_base_url)
        self.data = MarketDataService(settings.vnindex_csv, settings.data_source, self.ssi,
                                      settings.ssi_primary_market_data,
                                      settings.database_path.parent / "market_cache",
                                      settings.market_cache_hours,
                                      settings.screen_request_interval_seconds)
        self.analysis = AnalysisService(self.data, fundamental_cache_dir=settings.database_path.parent / "fundamental_cache",
                                        cache_hours=settings.market_cache_hours)
        self.users = UserStore(settings.database_path, settings.admin_telegram_id)
        self.ai = AIExplainer(settings.openai_api_key, settings.openai_model,
                              settings.deepseek_api_key, settings.deepseek_model)
        self.agent = AgentRouter(settings.openai_api_key, settings.openai_model)
        self.research = WebResearchService(settings.openai_api_key, settings.openai_model,
                                           settings.enable_web_research, settings.research_max_sources)
        self.backtester = BacktestEngine()
        self.position_risk = PositionRiskEngine()
        self.runtime = settings.database_path.parent
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.report_cache = {}
        self._screen_lock = asyncio.Lock()
        self._screen_cache_path = self.runtime / "screen_cache.json"
        self._screen_cache: dict[str, tuple[float, str]] = self._load_screen_cache()
        self._progress_updates: dict[tuple[int, int], float] = {}
        self._conversation_context: dict[tuple[int, int], dict] = {}

    def _load_screen_cache(self) -> dict[str, tuple[float, str]]:
        try:
            raw = json.loads(self._screen_cache_path.read_text(encoding="utf-8"))
            return {str(k): (float(v["created_at"]), str(v["text"]))
                    for k, v in raw.items() if isinstance(v, dict)}
        except (OSError, ValueError, TypeError, KeyError):
            return {}

    def _save_screen_cache(self) -> None:
        temp = self._screen_cache_path.with_suffix(".tmp")
        payload = {k: {"created_at": v[0], "text": v[1]} for k, v in self._screen_cache.items()}
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temp.replace(self._screen_cache_path)
        except OSError:
            log.warning("Cannot persist screen cache")
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    async def _safe_edit(self, message, text: str, parse_mode=None, retries: int = 3) -> bool:
        for attempt in range(retries):
            try:
                await message.edit_text(text[:4096], parse_mode=parse_mode)
                return True
            except BadRequest as exc:
                if "not modified" in str(exc).lower():
                    return True
                log.warning("Telegram rejected status update: %s", type(exc).__name__)
                return False
            except (NetworkError, TimedOut, RetryAfter) as exc:
                delay_value = getattr(exc, "retry_after", 2 ** attempt)
                delay = (delay_value.total_seconds() if hasattr(delay_value, "total_seconds")
                         else float(delay_value))
                log.warning("Telegram status update retry %s/%s: %s", attempt + 1, retries, type(exc).__name__)
                if attempt + 1 < retries:
                    await asyncio.sleep(min(delay, 8))
        return False

    async def _telegram_call(self, operation, label: str, retries: int = 3):
        """Retry Telegram delivery without turning a transport hiccup into an analysis failure."""
        for attempt in range(retries):
            try:
                return await operation()
            except BadRequest:
                log.exception("Telegram rejected %s", label)
                return None
            except (NetworkError, TimedOut, RetryAfter) as exc:
                delay_value = getattr(exc, "retry_after", 2 ** attempt)
                delay = (delay_value.total_seconds() if hasattr(delay_value, "total_seconds")
                         else float(delay_value))
                log.warning("Telegram %s retry %s/%s: %s", label, attempt + 1, retries,
                            type(exc).__name__)
                if attempt + 1 < retries:
                    await asyncio.sleep(min(delay, 8))
        return None

    async def _safe_delete(self, message) -> bool:
        result = await self._telegram_call(lambda: message.delete(), "delete", retries=3)
        return result is not None

    async def _safe_reply_text(self, message, text: str, parse_mode=None, **kwargs):
        return await self._telegram_call(
            lambda: message.reply_text(text[:4096], parse_mode=parse_mode, **kwargs), "send text", retries=3)

    async def _safe_reply_photo(self, message, path: Path, caption: str, parse_mode=None, **kwargs):
        async def send():
            with path.open("rb") as image:
                return await message.reply_photo(image, caption=caption[:1024], parse_mode=parse_mode, **kwargs)
        return await self._telegram_call(send, "send photo", retries=3)

    async def _safe_reply_document(self, message, path: Path, caption: str = ""):
        async def send():
            with path.open("rb") as document:
                return await message.reply_document(document, filename=path.name, caption=caption)
        return await self._telegram_call(send, "send document", retries=3)

    async def _progress_edit(self, message, text: str, force: bool = False) -> bool:
        key = (message.chat_id, message.message_id)
        now = time.monotonic()
        if not force and now - self._progress_updates.get(key, 0.0) < self.settings.telegram_progress_seconds:
            return True
        ok = await self._safe_edit(message, text, retries=2)
        if ok:
            self._progress_updates[key] = now
        return ok

    async def _deliver_screen_result(self, status, text: str) -> None:
        if not await self._safe_edit(status, text, ParseMode.HTML, retries=3):
            try:
                await status.reply_text(text[:4096], parse_mode=ParseMode.HTML)
            except (NetworkError, TimedOut, RetryAfter):
                log.warning("Screen result saved but Telegram delivery failed")

    @classmethod
    def _rankable_technical(cls, result) -> bool:
        """Keep one incomplete/new listing from aborting an entire universe scan."""
        if getattr(getattr(result, "signal", None), "value", None) == "BLOCKED":
            return False
        components = getattr(result, "components", {}) or {}
        return all(key in components and pd.notna(components[key]) for key in cls.SCREEN_COMPONENTS)

    def authorized(self, update: Update) -> bool:
        if not update.effective_user:
            return False
        chat_type = getattr(update.effective_chat, "type", "private")
        if chat_type in {"group", "supergroup"} and self.settings.allow_group_users:
            return True
        return self.users.approved(update.effective_user.id)

    async def require_auth(self, update: Update) -> bool:
        if self.authorized(update):
            return True
        await update.effective_message.reply_text(f"Bạn chưa được duyệt. Telegram user ID: <code>{update.effective_user.id}</code>", parse_mode=ParseMode.HTML)
        return False

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🇻🇳 Xu hướng VNINDEX", callback_data="menu:market"),
             InlineKeyboardButton("🔎 Tín hiệu VN100", callback_data="menu:signals")],
            [InlineKeyboardButton("📌 Phân tích một mã", callback_data="menu:analyze_help"),
             InlineKeyboardButton("🏢 Xếp hạng ngành", callback_data="menu:sector_help")],
            [InlineKeyboardButton("🧪 Hướng dẫn backtest", callback_data="menu:backtest_help"),
             InlineKeyboardButton("🛡 Quản trị danh mục", callback_data="menu:portfolio_help")],
            [InlineKeyboardButton("📚 Chiến lược & cách tính", callback_data="menu:strategy"),
             InlineKeyboardButton("❓ Toàn bộ lệnh", callback_data="menu:help")],
        ])
        await update.effective_message.reply_text(
            "📊 <b>TRỢ LÝ PHÂN TÍCH CHỨNG KHOÁN VIỆT NAM</b>\n\n"
            "Phân tích VNINDEX, xếp hạng VN100/ngành, đánh giá kỹ thuật–cơ bản, "
            "backtest và quản trị vị thế theo kỷ luật rủi ro.\n\n"
            "<b>Bắt đầu nhanh</b>\n"
            "• Gõ <code>phân tích FPT</code> để xem một mã.\n"
            "• Gõ <code>cổ phiếu có tín hiệu mua hôm nay</code> để quét VN100.\n"
            "• Gõ <code>dự báo thị trường sắp tới</code> để xem kịch bản VNINDEX.\n"
            "• Hoặc chọn chức năng bên dưới.\n\n"
            "<i>Tín hiệu dựa trên phiên ngày đã hoàn tất và luôn đi kèm điều kiện vô hiệu, không phải cam kết lợi nhuận.</i>",
            parse_mode=ParseMode.HTML, reply_markup=keyboard)

    async def menu_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        action = query.data.split(":", 1)[1]
        if action == "market":
            return await self.market_command(update, context)
        if action == "signals":
            context.args = ["VN100"]
            return await self.screen_command(update, context)
        if action == "strategy":
            context.args = ["strategy"]
            return await self.method_command(update, context)
        guides = {
            "analyze_help": ("📌 <b>PHÂN TÍCH MỘT MÃ</b>\n"
                             "Gõ <code>/analyze FPT</code> hoặc <code>phân tích FPT</code>.\n"
                             "Dùng <code>/technical FPT</code> hay <code>/fundamental FPT</code> nếu chỉ cần một phần."),
            "sector_help": ("🏢 <b>XẾP HẠNG NGÀNH</b>\n"
                            "Gõ <code>/sector ngân hàng</code>, <code>/sector bán lẻ</code> hoặc "
                            "<code>trong ngành ngân hàng mã nào dẫn đầu</code>."),
            "backtest_help": ("🧪 <b>BACKTEST</b>\n"
                              "Dùng <code>/backtest FPT 3m</code>, <code>6m</code>, <code>1y</code> hoặc <code>2y</code>.\n"
                              "Kết quả gồm điểm mua/bán, chi phí, VNINDEX, alpha và rủi ro."),
            "portfolio_help": ("🛡 <b>QUẢN TRỊ DANH MỤC</b>\n"
                               "Ghi nhận: <code>/position add FPT 1000 120</code>\n"
                               "Kiểm tra: <code>/position FPT</code> · Toàn bộ: <code>/portfolio</code>"),
            "help": ("❓ <b>LỆNH CHÍNH</b>\n"
                     "/market · /signals · /screen · /sector\n"
                     "/analyze · /compare · /technical · /fundamental · /live\n"
                     "/backtest · /position · /portfolio · /strategy · /data_status\n\n"
                     "Có thể dùng câu tiếng Việt tự nhiên và tag bot trong nhóm."),
        }
        await query.message.reply_text(guides[action], parse_mode=ParseMode.HTML)

    async def agent_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        chat_type = getattr(update.effective_chat, "type", "private")
        reply = message.reply_to_message
        replying_to_bot = bool(reply and reply.from_user and reply.from_user.id == context.bot.id)
        prompt = extract_agent_prompt(message.text or "", context.bot.username or "",
                                      chat_type == "private", replying_to_bot)
        if prompt is None:
            return
        if not await self.require_auth(update): return
        if not prompt:
            return await self.start(update, context)
        waiting = await self._safe_reply_text(update.effective_message, "Đang xử lý yêu cầu…")
        context_key = (update.effective_chat.id, update.effective_user.id)
        recent_context = self._conversation_context.get(context_key, {})
        plan = await asyncio.to_thread(self.agent.route, prompt, recent_context)
        await asyncio.to_thread(self.users.record_agent_event, update.effective_user.id,
                                prompt, plan["tool"], plan.get("via", "unknown"))
        if waiting is not None:
            await self._safe_delete(waiting)
        tool, args = plan["tool"], plan.get("args", {})
        routed_symbols = ([args.get("symbol")] if args.get("symbol") else args.get("symbols", []))
        routed_symbols = [str(x).upper() for x in routed_symbols if x]
        if routed_symbols:
            self._conversation_context[context_key] = {"last_symbol": routed_symbols[0], "last_tool": tool}
        if tool == "analyze_stock":
            context.args = [str(args.get("symbol", "")).upper()]
            return await self.analyze_command(update, context)
        if tool == "compare_stocks":
            context.args = [str(x).upper() for x in args.get("symbols", [])]
            return await self.compare_command(update, context)
        if tool == "backtest_stock":
            context.args = [str(args.get("symbol", "")).upper()]
            if args.get("lookback_months"):
                context.args.append(f"{int(args['lookback_months'])}m")
            return await self.backtest_command(update, context)
        if tool == "technical_analysis":
            context.args = [str(args.get("symbol", "")).upper()]
            return await self.technical_command(update, context)
        if tool == "fundamental_analysis":
            context.args = [str(args.get("symbol", "")).upper()]
            return await self.fundamental_command(update, context)
        if tool == "live_quote":
            context.args = [str(args.get("symbol", "")).upper()]
            return await self.live_command(update, context)
        if tool == "market_overview":
            return await self.market_command(update, context)
        if tool == "screen_watchlist":
            context.args = [str(args.get("universe") or self.settings.screen_universe).upper()]
            return await self.screen_command(update, context)
        if tool == "screen_sector":
            context.args = [str(args.get("industry") or "").strip()]
            return await self.sector_command(update, context)
        if tool == "position_add":
            context.args = ["add", str(args.get("symbol", "")).upper(),
                            str(args.get("quantity", "")), str(args.get("entry_price", ""))]
            return await self.position_command(update, context)
        if tool == "position_review":
            symbol = args.get("symbol") or recent_context.get("last_symbol", "")
            context.args = [str(symbol).upper()] if symbol else []
            return await self.position_command(update, context)
        if tool == "position_remove":
            context.args = ["remove", str(args.get("symbol", "")).upper()]
            return await self.position_command(update, context)
        if tool == "portfolio_status":
            return await self.portfolio_command(update, context)
        if tool == "explain_method":
            context.args = [str(args.get("topic") or "general")]
            return await self.method_command(update, context)
        return await self.start(update, context)

    async def compare_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_auth(update): return
        symbols = list(dict.fromkeys(x.upper() for x in context.args if x.isalpha()))[:4]
        if len(symbols) < 2:
            await update.effective_message.reply_text("Cú pháp: /compare FPT CMG")
            return
        status = await self._safe_reply_text(update.effective_message, "Đang so sánh " + ", ".join(symbols) + "…")
        if status is None: return
        rows, errors, expert_payload = [], [], []
        try:
            benchmark = await asyncio.wait_for(asyncio.to_thread(self.data.history, "VNINDEX", 500), timeout=75)
        except Exception as exc:
            await self._safe_edit(status, f"Không lấy được VNINDEX: {html.escape(str(exc))}", ParseMode.HTML)
            return

        async def compare_one(symbol):
            try:
                result = await asyncio.wait_for(asyncio.to_thread(self.analysis.analyze, symbol, benchmark), timeout=90)
                return symbol, result, None
            except Exception as exc:
                return symbol, None, type(exc).__name__

        results = await asyncio.gather(*(compare_one(symbol) for symbol in symbols))
        for symbol, result, error in results:
            if not error:
                _, tech, fund, final = result
                combined = None if fund.score is None else .8 * tech.score + .2 * fund.score
                rows.append((symbol, final.value, tech.score, fund.score, combined, tech.price))
                expert_payload.append({"symbol": symbol, "signal": final.value, "technical_score": tech.score,
                                       "fundamental_score": fund.score, "combined": combined,
                                       "strategy": tech.strategy, "reasons": tech.reasons,
                                       "fundamental_status": fund.status, "fundamental_metrics": fund.metrics})
            else:
                errors.append(symbol)
        rows.sort(key=lambda x: x[4] if x[4] is not None else -1, reverse=True)
        body = ["⚖️ <b>SO SÁNH ĐỊNH LƯỢNG</b>"]
        for rank, (symbol, signal, ts, fs, total, price) in enumerate(rows, 1):
            body.append(f"{rank}. <b>{symbol}</b> · {signal} · Giá {price:,.2f}\n"
                        f"   TA {ts:.1f} · FA {'N/A' if fs is None else f'{fs:.1f}'} · "
                        f"80/20 {'N/A' if total is None else f'{total:.1f}'}")
        if errors:
            body.append("\nKhông lấy được dữ liệu: " + ", ".join(errors))
        if expert_payload:
            opinion = await asyncio.to_thread(self.ai.explain, {"task": "compare", "companies": expert_payload})
            body.append("\n<b>GÓC NHÌN CHUYÊN GIA</b>\n" + html.escape(opinion))
        body.append("\n<i>Nguồn: Vnstock Quote/Fundamental, VCI. Xếp hạng chỉ so sánh các mã vừa yêu cầu và phụ thuộc độ phủ dữ liệu Free.</i>")
        await self._safe_edit(status, "\n".join(body)[:4096], ParseMode.HTML)

    async def market_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_auth(update): return
        status = await self._safe_reply_text(update.effective_message, "Đang đánh giá VNINDEX…")
        if status is None: return
        try:
            frame = await asyncio.to_thread(self.data.history, "VNINDEX", 500)
            self.data.assert_fresh(frame)
            outlook = analyze_market(frame)
            chart = await asyncio.to_thread(price_chart, frame, "VNINDEX", self.runtime / "vnindex_market.png")
            weights = outlook.weights
            text = (f"🇻🇳 <b>VNINDEX — {outlook.regime.value}</b>\n"
                    f"Thiên hướng {outlook.horizon}: <b>{outlook.bias}</b>\n"
                    f"Điểm xu hướng: {outlook.score:+.1f}/100 | Tin cậy: {outlook.confidence}\n"
                    f"Tỷ trọng cổ phiếu tham chiếu: <b>{outlook.exposure_min}–{outlook.exposure_max}%</b> danh mục\n"
                    f"Hỗ trợ 20 phiên: {outlook.support:,.2f}\nKháng cự 20 phiên: {outlook.resistance:,.2f}\n\n"
                    f"Trọng số: Trend {weights['trend']:.0%} · RSI {weights['momentum']:.0%} · "
                    f"Bollinger {weights['bollinger']:.0%} · Volume {weights['volume']:.0%}\n"
                    + "\n".join(f"• {html.escape(x)}" for x in outlook.drivers)
                    + "\n\n<i>Đây là kịch bản kỹ thuật, không phải dự báo chắc chắn.</i>")
            delivered = await self._safe_reply_photo(update.effective_message, chart, text, ParseMode.HTML)
            if delivered is not None:
                await self._safe_delete(status)
            else:
                await self._safe_edit(status,
                    "Đã hoàn tất đánh giá VNINDEX nhưng kết nối Telegram đang gián đoạn khi gửi biểu đồ. "
                    "Kết quả vẫn được giữ; hãy dùng /market để gửi lại.")
        except Exception as exc:
            log.exception("Market analysis failed")
            await self._safe_edit(status, f"Không thể đánh giá VNINDEX: {html.escape(str(exc))}", ParseMode.HTML)

    async def analyze_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_auth(update): return
        if not context.args:
            await update.effective_message.reply_text("Cú pháp: /analyze FPT")
            return
        symbol = context.args[0].upper()
        status = await self._safe_reply_text(update.effective_message, f"Đang phân tích {html.escape(symbol)}…")
        if status is None: return
        try:
            analysis_task = asyncio.to_thread(self.analysis.analyze, symbol)
            research_task = asyncio.to_thread(self.research.company_brief, symbol)
            (prices, tech, fund, final), research = await asyncio.gather(analysis_task, research_task)
            live = None
            if self.settings.ssi_configured:
                try:
                    live = await asyncio.wait_for(
                        asyncio.to_thread(self.data.intraday_snapshot, symbol), timeout=30)
                except Exception:
                    pass
            combined = None if fund.score is None else round(.8 * tech.score + .2 * fund.score, 1)
            ai_payload = {"technical": tech.to_dict(), "fundamental": {
                "score": fund.score, "status": fund.status, "components": fund.components,
                "metrics": fund.metrics, "missing": fund.missing}, "combined_80_20": combined,
                "final_signal": final.value, "recent_research": research.summary,
                "research_sources": research.sources}
            ai_text = await asyncio.to_thread(self.ai.explain, ai_payload)
            chart = await asyncio.to_thread(price_chart, prices, symbol, self.runtime / f"{symbol}_chart.png")
            fs = "N/A" if fund.score is None else f"{fund.score:.1f}/100"
            blend = "N/A" if combined is None else f"{combined:.1f}/100"
            live_line = ""
            if live and not pd.isna(live["time"]):
                live_line = (f"\nSSI 1 phút: {live['close']:,.2f} lúc {pd.Timestamp(live['time']):%H:%M %d/%m/%Y} "
                             f"· KL nến {live['volume']:,.0f}")
            price_source = html.escape(str(prices.attrs.get("source", "Vnstock Quote/VCI")))
            caption = (f"📌 <b>{html.escape(symbol)} — {final.value}</b>\n"
                    f"Phiên: {tech.as_of:%d/%m/%Y} | VNINDEX: {tech.regime.value}\n"
                    f"Kỹ thuật: {tech.score:.1f}/100 | Cơ bản: {fs} | Tổng hợp 80/20: {blend}\n"
                    f"Chiến lược: {html.escape(tech.strategy)}\n"
                    f"Giá: {tech.price:,.2f} | Hard stop: {tech.stop_loss or 0:,.2f} | Mốc 2R: {tech.take_profit or 0:,.2f}\n"
                    f"Khối lượng tối đa theo mô hình rủi ro: {tech.recommended_shares:,} cp"
                    f"{live_line}\nNguồn giá ngày: {price_source}\n"
                    "Báo cáo chi tiết ở tin nhắn kế tiếp.")
            technical_detail = technical_report(tech)
            fundamental_detail = fundamental_report(fund)
            user_id = update.effective_user.id
            self.report_cache[(user_id, symbol)] = {"technical": technical_detail, "fundamental": fundamental_detail,
                                                    "strategy": self._strategy_text()}
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("📈 Chi tiết kỹ thuật", callback_data=f"detail:technical:{symbol}"),
                InlineKeyboardButton("🏢 Chi tiết cơ bản", callback_data=f"detail:fundamental:{symbol}"),
            ], [InlineKeyboardButton("📚 Chiến lược & rủi ro", callback_data=f"detail:strategy:{symbol}")]])
            delivered = await self._safe_reply_photo(
                update.effective_message, chart, caption, ParseMode.HTML, reply_markup=keyboard)
            if delivered is None:
                await self._safe_edit(status,
                    f"Phân tích {html.escape(symbol)} đã hoàn tất nhưng Telegram chưa nhận được biểu đồ. "
                    f"Dùng /analyze {html.escape(symbol)} để gửi lại.", ParseMode.HTML)
                return
            await self._safe_delete(status)
            await self._safe_reply_text(update.effective_message,
                "🧭 <b>GÓC NHÌN CHUYÊN GIA</b>\n" + html.escape(ai_text) +
                "\n\n<i>Nhận định có điều kiện dựa trên dữ liệu được dẫn nguồn; không phải cam kết lợi nhuận.</i>",
                parse_mode=ParseMode.HTML)
            await self._safe_reply_text(update.effective_message, research_sources(research),
                                        parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception as exc:
            log.exception("Analyze failed")
            await self._safe_edit(status, f"Không thể phân tích: {html.escape(str(exc))}", ParseMode.HTML)

    async def live_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_auth(update): return
        if not context.args:
            await update.effective_message.reply_text("Cú pháp: /live FPT")
            return
        symbol = context.args[0].upper()
        status = await self._safe_reply_text(update.effective_message,
                                             f"Đang lấy dữ liệu SSI trong phiên cho {html.escape(symbol)}…")
        if status is None: return
        try:
            quote = await asyncio.wait_for(asyncio.to_thread(self.data.intraday_snapshot, symbol), timeout=35)
            stamp = quote.get("time")
            stamp_text = "Không xác định" if pd.isna(stamp) else f"{pd.Timestamp(stamp):%H:%M:%S %d/%m/%Y}"
            text = (f"⚡ <b>{html.escape(symbol)} — SSI TRONG PHIÊN</b>\n"
                    f"Cập nhật: {stamp_text}\n"
                    f"Close nến 1 phút: <b>{quote['close']:,.2f}</b>\n"
                    f"O/H/L: {quote['open']:,.2f} / {quote['high']:,.2f} / {quote['low']:,.2f}\n"
                    f"Khối lượng nến: {quote['volume']:,.0f}\n"
                    f"<i>Nguồn: {html.escape(quote['source'])}. Đây là dữ liệu trong phiên; tín hiệu chính vẫn xác nhận bằng nến ngày đã đóng.</i>")
            await self._safe_edit(status, text, ParseMode.HTML)
        except Exception as exc:
            await self._safe_edit(status, f"Không lấy được dữ liệu SSI realtime: {html.escape(str(exc))}", ParseMode.HTML)

    async def technical_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_auth(update): return
        if not context.args:
            await update.effective_message.reply_text("Cú pháp: /technical FPT")
            return
        symbol = context.args[0].upper()
        status = await self._safe_reply_text(update.effective_message,
                                             f"Đang phân tích kỹ thuật {html.escape(symbol)}…")
        if status is None: return
        try:
            prices = await asyncio.to_thread(self.data.history, symbol, 500)
            vni = await asyncio.to_thread(self.data.history, "VNINDEX", 500)
            self.data.assert_fresh(prices); self.data.assert_fresh(vni)
            from .technical import market_regime
            tech = self.analysis.technical.analyze(symbol, prices, market_regime(vni), vni)
            chart = await asyncio.to_thread(price_chart, prices, symbol, self.runtime / f"{symbol}_technical.png")
            text = (f"<b>{symbol} — {tech.signal.value}</b>\nPhiên: {tech.as_of:%d/%m/%Y} | VNINDEX: {tech.regime.value}\n"
                    f"Điểm kỹ thuật: {tech.score:.1f}/100 | {html.escape(tech.strategy)}\n"
                    f"Giá: {tech.price:,.2f} | Hard stop: {tech.stop_loss or 0:,.2f} | Mốc 2R: {tech.take_profit or 0:,.2f}\n"
                    f"{html.escape(' '.join(tech.reasons))}\n\nChỉ là phân tích kỹ thuật, không phải tín hiệu BUY hợp lưu.")
            delivered = await self._safe_reply_photo(update.effective_message, chart, text, ParseMode.HTML)
            if delivered is not None:
                await self._safe_delete(status)
            else:
                await self._safe_edit(status,
                    f"Phân tích kỹ thuật {html.escape(symbol)} đã hoàn tất nhưng Telegram chưa nhận được biểu đồ. "
                    f"Dùng /technical {html.escape(symbol)} để gửi lại.", ParseMode.HTML)
        except Exception as exc:
            log.exception("Technical analysis failed")
            await self._safe_edit(status, f"Không thể phân tích: {html.escape(str(exc))}", ParseMode.HTML)

    async def fundamental_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_auth(update): return
        if not context.args:
            await update.effective_message.reply_text("Cú pháp: /fundamental FPT")
            return
        symbol = context.args[0].upper()
        status = await self._safe_reply_text(update.effective_message,
                                             f"Đang đọc báo cáo tài chính {html.escape(symbol)}…")
        if status is None: return
        try:
            snapshot = await asyncio.wait_for(
                asyncio.to_thread(self.analysis.fundamentals.latest_as_of, symbol, date.today()), timeout=75)
            result = self.analysis.fundamental.analyze(symbol, snapshot)
            await self._safe_edit(status, fundamental_report(result)[:4096], ParseMode.HTML)
        except Exception as exc:
            log.exception("Fundamental analysis failed")
            await self._safe_edit(status, f"Không thể đọc dữ liệu cơ bản: {html.escape(str(exc))}",
                                  ParseMode.HTML)

    @staticmethod
    def _strategy_text() -> str:
        return ("<b>CHIẾN LƯỢC MOMENTUM–QUALITY CHO THỊ TRƯỜNG VIỆT NAM</b>\n\n"
                "<b>1. Bối cảnh thị trường</b>\n"
                "VNINDEX được phân loại bằng EMA20/50/200, RSI, Bollinger và khối lượng. "
                "UP mạnh giữ 80–100% cổ phiếu; UP yếu 60–75%; SIDEWAY 35–60%; DOWN 0–25%. "
                "Đây là trần phân bổ, không phải yêu cầu giải ngân hết ngay.\n\n"
                "<b>2. Xếp hạng cổ phiếu</b>\n"
                "Điểm tổng hợp gồm 80% kỹ thuật và 20% cơ bản. Trong 80 điểm kỹ thuật: "
                "sức mạnh tương đối 25 điểm, xu hướng 20, chất lượng điểm vào 15, "
                "khối lượng 10 và biến động/thanh khoản 10. Phần cơ bản kiểm tra Growth, Quality, "
                "Health và Valuation theo đúng loại hình doanh nghiệp. Khi screen, 25 điểm sức mạnh được tách thành "
                "15 điểm so với VNINDEX và 10 điểm so với peers; cơ bản giữ 60% điểm tuyệt đối và 40% percentile peers.\n\n"
                "<b>3. Vào lệnh và phân bổ</b>\n"
                "Ưu tiên nhóm xếp hạng cao có breakout kèm dòng tiền hoặc pullback giữ EMA20. "
                "Danh mục mục tiêu 6–8 mã; một mã khởi tạo 8–12%, tối đa 15%; một ngành tối đa 25–30%. "
                "Mỗi lệnh chịu rủi ro khoảng 0,6% tổng tài sản; tổng rủi ro mở không quá 4–5%.\n\n"
                "<b>4. Thoát lệnh</b>\n"
                "Hard stop là mức chặt hơn giữa −7% và 2 ATR. Trailing chỉ bật khi vị thế đồng thời đạt "
                "+1R và tối thiểu +5%; khoảng trailing là 3,25 ATR khi UP, 2 ATR khi SIDEWAY và 1,5 ATR khi DOWN. "
                "SELL nhẹ dùng để siết bảo vệ; mất EMA50 trong thị trường DOWN hoặc chạm stop mới là điều kiện thoát dứt khoát.\n\n"
                "<b>5. Cách đọc kết quả</b>\n"
                "BUY là setup đạt cổng kỹ thuật và chất lượng cơ bản; HOLD là lợi thế chưa đủ; "
                "BLOCKED là thiếu dữ liệu hoặc cơ bản không đạt; SELL là cảnh báo quản trị vị thế. "
                "Giá mua và stop là vùng tham chiếu theo dữ liệu cuối phiên, không phải lệnh tự động.\n\n"
                "<b>6. Tín hiệu hôm nay và dự báo</b>\n"
                "Tín hiệu mua/bán chỉ xác nhận bằng phiên ngày đã hoàn tất; nến đang chạy trong phiên không được dùng để kết luận. "
                "Dự báo VNINDEX là kịch bản xác suất cho 5–20 phiên và luôn đi kèm tỷ trọng danh mục tham chiếu.\n\n"
                "<i>Giới hạn kiểm định: dữ liệu Free chưa có ngày công bố báo cáo đầy đủ và lịch sử thành phần VN100 "
                "theo từng ngày. Vì vậy backtest một mã không đại diện cho toàn bộ danh mục luân chuyển.</i>")

    async def method_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_auth(update): return
        topic = (context.args[0] if context.args else "general").lower()
        texts = {
            "technical": ("<b>PHƯƠNG PHÁP KỸ THUẬT</b>\n"
                          "Hệ thống chấm sức mạnh tương đối với VNINDEX, EMA20/50/200, RSI14, Bollinger, "
                          "khối lượng tương đối và ATR. BUY là một setup "
                          "đã xác nhận; điểm số dùng để xếp hạng, không tự động biến thành lệnh mua."),
            "fundamental": ("<b>PHƯƠNG PHÁP CƠ BẢN</b>\n"
                            "Bốn nhóm Growth, Quality, Health và Valuation được chấm theo taxonomy doanh nghiệp. "
                            "Trọng số thay đổi theo ngành; ngân hàng không dùng máy móc D/E của doanh nghiệp sản xuất. "
                            "Điểm tổng hợp hiện tại dùng 80% kỹ thuật và 20% cơ bản."),
            "screen": ("<b>QUY TRÌNH SCREEN</b>\n"
                       "Tầng 1 xếp hạng toàn bộ rổ theo sức mạnh tương đối, xu hướng, điểm vào, dòng tiền và rủi ro. Tầng 2 đọc báo cáo tài chính cho nhóm dẫn đầu rồi ghép điểm 80/20. "
                       "Dữ liệu giá và cơ bản được lưu bền vững; SSI tải song song có giới hạn, Vnstock Free dùng hàng đợi riêng. "
                       "Kết quả được dùng chung trong 30 phút. Lỗi cập nhật tiến độ Telegram không làm hủy lượt quét."),
            "backtest": ("<b>NGUYÊN TẮC BACKTEST</b>\n"
                         "Tín hiệu cuối phiên được thực hiện ở Open phiên kế tiếp. Kết quả đã trừ phí, thuế và trượt giá. "
                         "Hard stop giới hạn rủi ro ban đầu; trailing chỉ bật khi đồng thời đạt +1R và +5%, sau đó đi theo đỉnh và ATR. "
                         "Stop mới tính sau Close chỉ có hiệu lực từ phiên kế tiếp để tránh nhìn trước dữ liệu."),
            "risk": ("<b>QUẢN TRỊ VỊ THẾ</b>\n"
                     "Hard stop lấy mức chặt hơn giữa −7% và 2 ATR. Khi đồng thời đạt +1R và +5%, trailing stop được bật. "
                     "Hệ số ATR là 3,25 khi UP; 2,0 khi SIDEWAY; 1,5 khi DOWN. Stop chỉ được nâng lên. "
                     "SELL nhẹ chuyển sang theo dõi hoặc siết bảo vệ; EXIT chỉ xuất hiện khi điều kiện thoát thực sự bị vi phạm."),
            "data": ("<b>NGUỒN DỮ LIỆU</b>\n"
                     "Giá, thành phần chỉ số và dữ liệu trong phiên ưu tiên SSI FastConnect Data; Vnstock/VCI là nguồn dự phòng. "
                     "Báo cáo tài chính lấy từ Vnstock Finance/VCI. Mỗi báo cáo hiển thị kỳ dữ liệu và ngày truy xuất."),
            "strategy": self._strategy_text(),
            "general": self._strategy_text(),
        }
        await update.effective_message.reply_text(texts.get(topic, texts["general"]), parse_mode=ParseMode.HTML)

    async def screen_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_auth(update): return
        universe = (context.args[0] if context.args else self.settings.screen_universe).strip().upper()
        if universe in {"WATCHLIST", "WL"}:
            universe = "WATCHLIST"
        elif universe not in {"VN30", "VN100"}:
            await update.effective_message.reply_text("Rổ hỗ trợ: /screen VN100, /screen VN30 hoặc /screen watchlist")
            return
        status = await self._safe_reply_text(update.effective_message, f"🔎 Đang chuẩn bị quét {universe}…")
        if status is None: return
        cached = self._screen_cache.get(universe)
        cache_ttl = self.settings.screen_cache_minutes * 60
        if cached and time.time() - cached[0] < cache_ttl:
            await self._deliver_screen_result(status, cached[1])
            return
        if self._screen_lock.locked():
            await self._progress_edit(status, "⏳ Một lượt screen đang chạy. Yêu cầu này sẽ tiếp tục ngay sau đó…", True)
        async with self._screen_lock:
            cached = self._screen_cache.get(universe)
            if cached and time.time() - cached[0] < cache_ttl:
                await self._deliver_screen_result(status, cached[1])
                return
            try:
                text = await self._run_screen(universe, status)
                self._screen_cache[universe] = (time.time(), text)
                self._save_screen_cache()
                await self._deliver_screen_result(status, text)
            except Exception as exc:
                log.exception("Screen failed")
                stale = self._screen_cache.get(universe)
                if stale:
                    await self._deliver_screen_result(status, stale[1] +
                        "\n\n<i>Đang hiển thị kết quả lưu gần nhất vì nguồn dữ liệu tạm thời gián đoạn.</i>")
                else:
                    await self._safe_edit(status, "Nguồn dữ liệu đang gián đoạn. Chưa có kết quả lưu để hiển thị.",
                                          ParseMode.HTML)

    async def sector_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_auth(update): return
        query = " ".join(context.args or []).strip()
        if not query:
            await update.effective_message.reply_text("Cú pháp: /sector bán lẻ")
            return
        cache_key = "SECTOR:" + query.casefold()
        status = await self._safe_reply_text(update.effective_message, f"Đang xác định nhóm ngành {query}…")
        if status is None: return
        cached = self._screen_cache.get(cache_key)
        cache_ttl = self.settings.screen_cache_minutes * 60
        if cached and time.time() - cached[0] < cache_ttl:
            await self._deliver_screen_result(status, cached[1])
            return
        if self._screen_lock.locked():
            await self._progress_edit(status, "Một lượt xếp hạng đang chạy. Yêu cầu này sẽ tiếp tục ngay sau đó…", True)
        async with self._screen_lock:
            cached = self._screen_cache.get(cache_key)
            if cached and time.time() - cached[0] < cache_ttl:
                await self._deliver_screen_result(status, cached[1])
                return
            try:
                industry, symbols = await asyncio.wait_for(
                    asyncio.to_thread(self.data.symbols_by_industry, query), timeout=75)
                label = f"NGÀNH {industry.upper()}"
                text = await self._run_screen(label, status, symbols)
                self._screen_cache[cache_key] = (time.time(), text)
                self._save_screen_cache()
                await self._deliver_screen_result(status, text)
            except Exception as exc:
                log.exception("Sector screen failed")
                stale = self._screen_cache.get(cache_key)
                if stale:
                    await self._deliver_screen_result(status, stale[1] +
                        "\n\n<i>Đang hiển thị kết quả lưu gần nhất vì nguồn dữ liệu tạm thời gián đoạn.</i>")
                else:
                    await self._safe_edit(status, "Nguồn dữ liệu ngành đang gián đoạn. Chưa có kết quả lưu để hiển thị.",
                                          ParseMode.HTML)

    async def _run_screen(self, universe: str, status, symbols_override: list[str] | None = None) -> str:
        if symbols_override is not None:
            symbols = list(dict.fromkeys(symbols_override))
        elif universe == "WATCHLIST":
            symbols = list(dict.fromkeys(self.settings.watchlist))
        else:
            symbols = await asyncio.wait_for(asyncio.to_thread(self.data.symbols_by_group, universe), timeout=90)
        await self._progress_edit(status, f"🔎 {universe} · tầng 1 kỹ thuật: 0/{len(symbols)} mã…", True)
        found, ranked, errors, technical_rows = [], [], [], []
        benchmark = await asyncio.wait_for(asyncio.to_thread(self.data.history, "VNINDEX", 500), timeout=75)
        self.data.assert_fresh(benchmark)
        from .technical import market_regime
        regime = market_regime(benchmark)
        workers = max(1, self.settings.screen_ssi_concurrency if self.settings.ssi_configured else 1)
        semaphore = asyncio.Semaphore(workers)

        async def technical_one(symbol):
            async with semaphore:
                try:
                    prices = await asyncio.wait_for(asyncio.to_thread(self.data.history, symbol, 260), timeout=75)
                    self.data.assert_fresh(prices)
                    tech = self.analysis.technical.analyze(symbol, prices, regime, benchmark)
                    if not self._rankable_technical(tech):
                        return symbol, None, "InsufficientTechnicalHistory"
                    return symbol, tech, None
                except Exception as exc:
                    return symbol, None, type(exc).__name__

        technical_tasks = [asyncio.create_task(technical_one(symbol)) for symbol in symbols]
        for completed, future in enumerate(asyncio.as_completed(technical_tasks), 1):
            symbol, tech, error = await future
            if error:
                errors.append(symbol)
            else:
                technical_rows.append((symbol, tech))
            if completed == len(symbols) or completed % 5 == 0:
                await self._progress_edit(
                    status, f"🔎 {universe} · tầng 1 kỹ thuật: {completed}/{len(symbols)} · lỗi {len(errors)}…")

        if not technical_rows:
            raise DataError("Không mã nào có đủ dữ liệu kỹ thuật")

        # Split relative strength into market-relative (60%) and peer-relative
        # (40%). On /sector this is a true industry comparison; on VN100 it is
        # the cross-sectional rank of the selected universe.
        peer_raw = pd.Series({symbol: .6 * float(tech.indicators.get("return63") or 0)
                              + .4 * float(tech.indicators.get("return126") or 0)
                              for symbol, tech in technical_rows})
        peer_percentile = peer_raw.rank(method="average", pct=True) * 100
        for symbol, tech in technical_rows:
            peer_score = float(peer_percentile.get(symbol, 50.0))
            market_score = float(tech.components.get("relative_strength", 50.0))
            blended_rs = .6 * market_score + .4 * peer_score
            tech.components["market_strength"] = round(market_score, 1)
            tech.components["peer_strength"] = round(peer_score, 1)
            tech.components["relative_strength"] = round(blended_rs, 1)
            weights = {"relative_strength": .3125, "trend": .25, "entry_quality": .1875,
                       "volume": .125, "risk_liquidity": .125}
            tech.score = round(sum(float(tech.components.get(k, 0.0)) * weight
                                   for k, weight in weights.items()), 1)

        technical_rows.sort(key=lambda item: item[1].score, reverse=True)
        adaptive_limit = max(5, min(10, (len(technical_rows) * 3 + 9) // 10))
        limit = min(max(1, self.settings.screen_fundamental_limit), adaptive_limit, len(technical_rows))
        shortlist = technical_rows[:limit]
        semaphore = asyncio.Semaphore(3)
        fundamental_results = []

        async def fundamental_one(symbol, tech):
            async with semaphore:
                try:
                    snapshot = await asyncio.wait_for(
                        asyncio.to_thread(self.analysis.fundamentals.latest_as_of, symbol, date.today()), timeout=45)
                    fund = self.analysis.fundamental.analyze(symbol, snapshot)
                    return symbol, tech, fund, None
                except Exception as exc:
                    return symbol, tech, None, type(exc).__name__

        tasks = [asyncio.create_task(fundamental_one(symbol, tech)) for symbol, tech in shortlist]
        completed = 0
        try:
            for future in asyncio.as_completed(tasks, timeout=180):
                symbol, tech, fund, error = await future
                completed += 1
                if error:
                    errors.append(symbol)
                else:
                    fundamental_results.append((symbol, tech, fund))
                if completed == len(shortlist) or completed % 3 == 0:
                    await self._progress_edit(
                        status, f"🧾 {universe} · tầng 2 cơ bản: {completed}/{len(shortlist)} · lỗi tổng {len(set(errors))}…")
        except TimeoutError:
            pass
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            completed_symbols = {row[0] for row in fundamental_results} | set(errors)
            errors.extend(symbol for symbol, _ in shortlist if symbol not in completed_symbols)

        # Peer-normalize the four fundamental pillars. The original absolute
        # score remains 60% so a weak sector cannot crown its least-weak member.
        peer_components = {}
        for component in ("growth", "quality", "health", "valuation"):
            values = {symbol: fund.components.get(component) for symbol, _, fund in fundamental_results
                      if isinstance(fund.components.get(component), (int, float))}
            series = pd.Series(values, dtype=float)
            peer_components[component] = ((series.rank(method="average", pct=True) * 100).to_dict()
                                          if not series.empty else {})

        def fundamental_weights(fund):
            company_type = str(fund.metrics.get("company_type") or "CT")
            if company_type == "NH": return {"growth": .25, "quality": .30, "health": .30, "valuation": .15}
            if company_type == "CK": return {"growth": .30, "quality": .30, "health": .15, "valuation": .25}
            if company_type == "BH": return {"growth": .20, "quality": .30, "health": .25, "valuation": .25}
            return {"growth": .35, "quality": .25, "health": .20, "valuation": .20}

        for symbol, tech, fund in fundamental_results:
            if fund.score is None:
                peer_fund = adjusted_fund = total = None
                final_value = "BLOCKED" if tech.signal.value == "BUY" else tech.signal.value
            else:
                weights = fundamental_weights(fund)
                available = {key: peer_components[key].get(symbol) for key in weights
                             if peer_components[key].get(symbol) is not None}
                active_weight = sum(weights[key] for key in available)
                peer_fund = (sum(available[key] * weights[key] for key in available) / active_weight
                             if active_weight else float(fund.score))
                adjusted_fund = .6 * float(fund.score) + .4 * peer_fund
                total = .8 * tech.score + .2 * adjusted_fund
                quality = fund.components.get("quality")
                health = fund.components.get("health")
                quality_gate = ((not isinstance(quality, (int, float)) or quality >= 35) and
                                (not isinstance(health, (int, float)) or health >= 35))
                final_value = tech.signal.value
                if tech.signal.value == "BUY" and (adjusted_fund < 40 or total < 60 or not quality_gate):
                    final_value = "BLOCKED"
            row = {"symbol": symbol, "signal": final_value, "technical": tech.score,
                   "fundamental": fund.score, "peer_fundamental": peer_fund,
                   "adjusted_fundamental": adjusted_fund, "total": total,
                   "components": tech.components, "price": tech.price,
                   "stop": tech.stop_loss, "strategy": tech.strategy, "as_of": tech.as_of}
            ranked.append(row)
            if final_value == "BUY":
                found.append(row)

        ranked.sort(key=lambda x: x["total"] if x["total"] is not None else -1, reverse=True)
        found.sort(key=lambda x: x["total"] if x["total"] is not None else -1, reverse=True)
        outlook = analyze_market(benchmark)
        breadth = (sum(1 for _, tech in technical_rows
                       if tech.price > float(tech.indicators.get("ema50") or float("inf")))
                   / len(technical_rows) * 100 if technical_rows else 0.0)
        exposure_min, exposure_max = outlook.exposure_min, outlook.exposure_max
        if breadth < 35:
            exposure_min, exposure_max = 0, min(exposure_max, 25)
        elif breadth < 50:
            exposure_min, exposure_max = min(exposure_min, 25), min(exposure_max, 50)
        latest_session = max(tech.as_of for _, tech in technical_rows)
        body = [f"🔎 <b>KẾT QUẢ SCREEN {html.escape(universe)}</b>",
                f"Tín hiệu theo phiên đóng cửa {latest_session:%d/%m/%Y}.",
                f"VNINDEX {regime.value} · độ rộng trên EMA50 {breadth:.0f}% · tỷ trọng cổ phiếu tham chiếu {exposure_min}–{exposure_max}%.",
                f"Kỹ thuật: {len(technical_rows)}/{len(symbols)} mã · Cơ bản/peers: {len(ranked)}/{len(shortlist)} mã shortlist."]
        if found:
            body.append("\n<b>Tín hiệu mua đạt điều kiện:</b>")
            for row in found[:5]:
                c = row["components"]
                body.append(f"• <b>{row['symbol']}</b>: Tổng {row['total']:.1f} · TA {row['technical']:.1f} · "
                            f"FA peer {row['adjusted_fundamental']:.1f}\n"
                            f"  Giá xác nhận {row['price']:,.2f} · Stop {row['stop'] or 0:,.2f} · {html.escape(row['strategy'])}\n"
                            f"  So VNINDEX {c.get('market_strength', 0):.0f} · So peers {c.get('peer_strength', 0):.0f}")
        else:
            body.append("Không có mã đạt toàn bộ điều kiện BUY trong dữ liệu hoàn tất.")
            candidates = [x for x in ranked if x["total"] is not None][:5]
            if candidates:
                body.append("\n<b>Mã gần điều kiện nhất:</b>")
                for row in candidates:
                    c = row["components"]
                    body.append(f"• {row['symbol']}: {row['signal']} · Tổng {row['total']:.1f} · "
                                f"TA {row['technical']:.1f} · FA peer {row['adjusted_fundamental']:.1f}\n"
                                f"  So VNINDEX {c.get('market_strength', 0):.0f} · So peers {c.get('peer_strength', 0):.0f}")
        sell_warnings = [(symbol, tech) for symbol, tech in technical_rows if tech.signal.value == "SELL"][:3]
        if sell_warnings:
            body.append("\n<b>Cảnh báo suy yếu cho vị thế đang nắm giữ:</b>")
            for symbol, tech in sell_warnings:
                body.append(f"• {symbol}: SELL warning tại {tech.price:,.2f} · mức bảo vệ tham chiếu {tech.stop_loss or 0:,.2f}")
        unique_errors = list(dict.fromkeys(errors))
        if unique_errors:
            body.append(f"\nThiếu dữ liệu/timeout {len(unique_errors)} mã: {', '.join(unique_errors[:20])}")
        market_source = ("SSI FastConnect Data ưu tiên, Vnstock/VCI dự phòng"
                         if self.settings.ssi_configured else "Vnstock/VCI")
        body.append(f"\n<i>Universe và giá: {market_source}; cơ bản: Vnstock Finance/VCI. "
                    f"FA peer được chuẩn hóa trong {len(ranked)} mã shortlist có đủ dữ liệu. "
                    "Giá xác nhận là dữ liệu cuối phiên, không phải lệnh khớp chắc chắn.</i>")
        return "\n".join(body)[:4096]

    async def position_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_auth(update): return
        args = list(context.args or [])
        if not args:
            return await self.portfolio_command(update, context)
        operation = args[0].lower()
        if operation in {"add", "them"}:
            if len(args) < 4:
                await update.effective_message.reply_text(
                    "Cú pháp: /position add FPT 1000 120\nTrong đó 1000 là số cổ phiếu và 120 là giá vốn theo đơn vị giá bot hiển thị.")
                return
            symbol = args[1].upper()
            try:
                quantity = int(float(args[2].replace(",", "")))
                entry_price = float(args[3].replace(",", ""))
            except ValueError:
                await update.effective_message.reply_text("Số lượng và giá vốn phải là số hợp lệ.")
                return
            if not re.fullmatch(r"[A-Z]{3}", symbol) or quantity <= 0 or entry_price <= 0:
                await update.effective_message.reply_text("Mã, số lượng hoặc giá vốn không hợp lệ.")
                return
            status = await self._safe_reply_text(update.effective_message, f"Đang kiểm tra dữ liệu {symbol}…")
            if status is None: return
            try:
                prices = await asyncio.wait_for(asyncio.to_thread(self.data.history, symbol, 260), timeout=75)
                latest = float(prices.iloc[-1].close)
                if entry_price > latest * 100:
                    entry_price /= 1000
                elif latest > entry_price * 100:
                    entry_price *= 1000
                await asyncio.to_thread(self.users.upsert_position, update.effective_user.id,
                                        update.effective_chat.id, symbol, quantity, entry_price)
                self._conversation_context[(update.effective_chat.id, update.effective_user.id)] = {
                    "last_symbol": symbol, "last_tool": "position_review"}
                await self._safe_edit(status,
                    f"Đã ghi nhận <b>{symbol}</b>: {quantity:,} cp · giá vốn {entry_price:,.2f}.\nĐang tính mức bảo vệ vị thế…",
                    ParseMode.HTML)
                return await self._position_review(update, symbol, status)
            except Exception as exc:
                log.exception("Position add failed")
                await self._safe_edit(status, f"Không thể ghi nhận vị thế: {html.escape(str(exc))}", ParseMode.HTML)
                return
        if operation in {"remove", "close", "xoa", "dong"}:
            if len(args) < 2:
                await update.effective_message.reply_text("Cú pháp: /position remove FPT")
                return
            symbol = args[1].upper()
            removed = await asyncio.to_thread(self.users.remove_position, update.effective_user.id, symbol)
            await update.effective_message.reply_text(
                f"Đã đóng theo dõi vị thế {symbol}." if removed else f"Không tìm thấy vị thế {symbol}.")
            return
        symbol = operation.upper()
        if not re.fullmatch(r"[A-Z]{3}", symbol):
            await update.effective_message.reply_text("Cú pháp: /position FPT hoặc /position add FPT 1000 120")
            return
        status = await self._safe_reply_text(update.effective_message, f"Đang cập nhật quản trị vị thế {symbol}…")
        if status is None: return
        await self._position_review(update, symbol, status)

    async def _position_assessment(self, user_id: int, symbol: str):
        position = await asyncio.to_thread(self.users.get_position, user_id, symbol)
        if not position:
            raise ValueError(f"Chưa ghi nhận vị thế {symbol}. Dùng /position add {symbol} 1000 GIÁ_VỐN")
        prices_task = asyncio.to_thread(self.data.history, symbol, 500)
        market_task = asyncio.to_thread(self.data.history, "VNINDEX", 500)
        prices, benchmark = await asyncio.gather(prices_task, market_task)
        self.data.assert_fresh(prices); self.data.assert_fresh(benchmark)
        from .technical import market_regime
        technical = self.analysis.technical.analyze(symbol, prices, market_regime(benchmark), benchmark)
        live_price = live_high = None
        if self.settings.ssi_configured:
            try:
                live = await asyncio.wait_for(asyncio.to_thread(self.data.intraday_snapshot, symbol), timeout=20)
                live_price, live_high = float(live["close"]), float(live["high"])
            except Exception:
                pass
        assessment = self.position_risk.assess(position, technical, live_price, live_high)
        return position, assessment

    @staticmethod
    def _position_text(assessment) -> str:
        labels = {"HOLD": "TIẾP TỤC NẮM GIỮ", "WATCH": "THEO DÕI SÁT",
                  "PROTECT": "SIẾT MỨC BẢO VỆ", "EXIT": "THOÁT VỊ THẾ"}
        return (
            f"🛡 <b>QUẢN TRỊ VỊ THẾ — {assessment.symbol}</b>\n"
            f"Trạng thái: <b>{labels[assessment.action]}</b>\n"
            f"Giá vốn: {assessment.entry_price:,.2f} · Giá hiện tại: {assessment.price:,.2f}\n"
            f"Lãi/lỗ tạm tính: <b>{assessment.pnl_pct:+.2f}%</b> · R: {assessment.risk_multiple:+.2f}\n"
            f"Đỉnh từ khi ghi nhận: {assessment.high_watermark:,.2f}\n"
            f"Mức bảo vệ hiện tại: <b>{assessment.stop_price:,.2f}</b>\n"
            f"Trailing stop: {'ĐÃ KÍCH HOẠT' if assessment.trailing_active else 'CHƯA KÍCH HOẠT'}\n"
            f"Tín hiệu kỹ thuật: {assessment.technical_signal.value} · VNINDEX: {assessment.regime.value}\n\n"
            f"{html.escape(assessment.reason)}\n"
            "<i>Trailing chỉ bật khi vị thế đồng thời đạt +1R và +5%. Mức bảo vệ chỉ được nâng lên, "
            "không hạ xuống. Giá gap có thể khiến giá thực hiện thấp hơn mức dự kiến.</i>")

    async def _position_review(self, update: Update, symbol: str, status):
        try:
            _, assessment = await self._position_assessment(update.effective_user.id, symbol)
            await asyncio.to_thread(self.users.update_position_risk, update.effective_user.id, symbol,
                                    assessment.high_watermark, assessment.stop_price,
                                    assessment.price, assessment.action)
            await self._safe_edit(status, self._position_text(assessment), ParseMode.HTML)
        except Exception as exc:
            await self._safe_edit(status, html.escape(str(exc)), ParseMode.HTML)

    async def portfolio_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_auth(update): return
        positions = await asyncio.to_thread(self.users.list_positions, update.effective_user.id)
        if not positions:
            await update.effective_message.reply_text(
                "Danh mục theo dõi đang trống. Dùng /position add FPT 1000 120 để ghi nhận vị thế.")
            return
        lines = ["📁 <b>DANH MỤC ĐANG THEO DÕI</b>"]
        for item in positions:
            last = item.get("last_price")
            pnl = ((float(last) / item["entry_price"] - 1) * 100) if last else None
            lines.append(f"• <b>{item['symbol']}</b>: {item['quantity']:,} cp · vốn {item['entry_price']:,.2f} · "
                         f"{'chưa cập nhật' if pnl is None else f'{pnl:+.2f}%'} · stop "
                         f"{item['active_stop']:,.2f}")
        lines.append("\nDùng /position MÃ để cập nhật giá và mức bảo vệ.")
        await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def monitor_positions(self, context: ContextTypes.DEFAULT_TYPE):
        now = pd.Timestamp.now(tz="Asia/Bangkok")
        if now.weekday() >= 5 or not (9 <= now.hour < 15 or (now.hour == 15 and now.minute <= 15)):
            return
        positions = await asyncio.to_thread(self.users.list_all_positions)
        for position in positions:
            try:
                previous_action = position.get("last_action")
                _, assessment = await self._position_assessment(position["user_id"], position["symbol"])
                await asyncio.to_thread(self.users.update_position_risk, position["user_id"], position["symbol"],
                                        assessment.high_watermark, assessment.stop_price,
                                        assessment.price, assessment.action)
                if assessment.action in {"WATCH", "PROTECT", "EXIT"} and assessment.action != previous_action:
                    await context.bot.send_message(position["chat_id"], self._position_text(assessment),
                                                   parse_mode=ParseMode.HTML)
            except Exception:
                log.exception("Position monitor failed for %s", position.get("symbol"))

    async def post_init(self, application: Application):
        interval = max(1, self.settings.position_monitor_minutes) * 60
        application.job_queue.run_repeating(self.monitor_positions, interval=interval, first=30,
                                            name="position-monitor")
        commands = [
            BotCommand("start", "Mở bảng điều khiển"),
            BotCommand("market", "Xu hướng VNINDEX"),
            BotCommand("signals", "Tín hiệu mua bán VN100"),
            BotCommand("analyze", "Phân tích một cổ phiếu"),
            BotCommand("sector", "Xếp hạng cổ phiếu theo ngành"),
            BotCommand("compare", "So sánh các cổ phiếu"),
            BotCommand("backtest", "Kiểm định chiến lược trong quá khứ"),
            BotCommand("portfolio", "Danh mục đang theo dõi"),
            BotCommand("strategy", "Chiến lược và cách tính điểm"),
            BotCommand("data_status", "Kiểm tra thời điểm dữ liệu"),
        ]
        try:
            await application.bot.set_my_commands(commands)
        except (NetworkError, TimedOut, RetryAfter) as exc:
            log.warning("Could not update Telegram command menu: %s", type(exc).__name__)

    async def backtest_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_auth(update): return
        if not context.args:
            await update.effective_message.reply_text(
                "Cú pháp: /backtest FPT 3m, /backtest FPT 6m, /backtest FPT 1y\n"
                "Hoặc nhắn: backtest FPT trong 3 tháng gần đây")
            return
        symbol = context.args[0].upper()
        months = extract_backtest_months(" ".join(context.args[1:]))
        period_text = f"{months // 12} năm" if months and months % 12 == 0 else (f"{months} tháng" if months else "lịch sử tối đa (~1.500 phiên)")
        status = await self._safe_reply_text(update.effective_message,
                                             f"Đang backtest {symbol} · {period_text}…")
        if status is None: return
        try:
            rows = min(3000, max(260, int(months * 23 + 180))) if months else 1500
            prices = await asyncio.to_thread(self.data.history, symbol, rows)
            vni = await asyncio.to_thread(self.data.history, "VNINDEX", rows)
            price_source = prices.attrs.get("source", "Nguồn OHLCV đã cấu hình")
            evaluation_start = (pd.Timestamp(prices.iloc[-1].time) - pd.DateOffset(months=months)).normalize() if months else None
            result = await asyncio.to_thread(self.backtester.run, symbol, prices, vni, True, 100_000_000, evaluation_start)
            chart = await asyncio.to_thread(backtest_chart, prices, result, symbol, self.runtime / f"{symbol}_backtest.png")
            trade_csv = self.runtime / f"{symbol}_backtest_trades.csv"
            await asyncio.to_thread(result.trade_log.to_csv, trade_csv, index=False, encoding="utf-8-sig")
            pf = "∞" if result.profit_factor == float("inf") else f"{result.profit_factor:.2f}"
            short_window_note = ("\n⚠️ Khoảng kiểm định dưới 6 tháng có thể có rất ít lệnh; "
                                 "không nên dùng riêng kết quả này để kết luận chiến lược."
                                 if months and months < 6 else "")
            caption = (f"<b>Backtest {symbol} · {period_text}</b>\nKỳ kiểm định thực tế: {result.equity.iloc[0].date:%d/%m/%Y}–{result.equity.iloc[-1].date:%d/%m/%Y}\n"
                       f"Lợi nhuận: {result.total_return_pct:.2f}% | VNINDEX: {result.benchmark_return_pct:.2f}% | Alpha: {result.alpha_pct:+.2f}%\n"
                       f"CAGR: {result.cagr_pct:.2f}% | Calmar: {result.calmar:.2f} | Tỷ trọng vốn TB: {result.average_exposure_pct:.1f}%\n"
                       f"MDD: {result.max_drawdown_pct:.2f}% | Sharpe: {result.sharpe:.2f} | Sortino: {result.sortino:.2f}\n"
                       f"Phiên có BUY: {result.buy_signal_sessions} | Lệnh hoàn tất: {result.trades} | "
                       f"Bỏ qua do giới hạn vốn: {result.rejected_entries}\n"
                       f"Win rate: {result.win_rate_pct:.1f}% | PF: {pf} | Kỳ vọng/lệnh: {result.expectancy_pct:+.2f}%\n"
                       "▲ xanh: mua ở Open kế tiếp · ▼/X: trailing stop, hard stop hoặc tín hiệu bán xác nhận.\n"
                       "Hard stop bảo vệ tối đa khoảng 7%; trailing chỉ bật khi đồng thời đạt +1R và +5%.\n"
                       "SELL xác nhận thoát vị thế chưa bật trailing; nếu trailing đã bật thì tín hiệu dùng để siết bảo vệ.\n"
                       "Time stop thoát sau 20 phiên nếu giá chưa đi được 0,5R. Mức stop mới có hiệu lực từ phiên kế tiếp.\n"
                       "Backtest mô phỏng khối lượng nguyên từ 1 cổ phiếu để không làm mất tín hiệu ở mã thị giá cao; "
                       "lệnh odd-lot thực tế có thể có thanh khoản và giá khớp khác.\n"
                       f"Nguồn giá: {html.escape(str(price_source))}. Giả định FS đạt chuẩn toàn kỳ; chưa phải backtest hợp lưu point-in-time."
                       f"{short_window_note}")
            opinion = await asyncio.to_thread(self.ai.explain, {"task": "backtest", "symbol": symbol,
                "total_return_pct": result.total_return_pct, "cagr_pct": result.cagr_pct,
                "max_drawdown_pct": result.max_drawdown_pct, "sharpe": result.sharpe,
                "sortino": result.sortino, "win_rate_pct": result.win_rate_pct,
                "profit_factor": result.profit_factor, "trades": result.trades,
                "buy_signal_sessions": result.buy_signal_sessions,
                "rejected_entries": result.rejected_entries,
                "known_limit": "fundamental_pass assumed true for full period"})
            delivered = await self._safe_reply_photo(update.effective_message, chart, caption, ParseMode.HTML)
            if delivered is None:
                await self._safe_edit(status,
                    f"Backtest {html.escape(symbol)} đã hoàn tất nhưng Telegram chưa nhận được biểu đồ. "
                    f"Dùng /backtest {html.escape(symbol)} để gửi lại.", ParseMode.HTML)
                return
            await self._safe_delete(status)
            if not result.trade_log.empty:
                recent = result.trade_log.tail(10)
                trade_lines = ["📋 <b>10 GIAO DỊCH GẦN NHẤT</b>"]
                for idx, row in recent.iterrows():
                    trade_lines.append(
                        f"#{idx + 1} {pd.Timestamp(row.entry_date):%d/%m/%Y} @ {row.entry:,.2f} → "
                        f"{pd.Timestamp(row.exit_date):%d/%m/%Y} @ {row.exit:,.2f} · "
                        f"{row.return_pct:+.2f}% · {html.escape(str(row.reason))}")
                await self._safe_reply_text(update.effective_message, "\n".join(trade_lines),
                                            parse_mode=ParseMode.HTML)
            await self._safe_reply_document(update.effective_message, trade_csv,
                "Toàn bộ lịch sử điểm mua/bán và lợi nhuận từng lệnh.")
            await self._safe_reply_text(update.effective_message,
                "🧭 <b>ĐÁNH GIÁ CHUYÊN GIA SAU BACKTEST</b>\n" + html.escape(opinion),
                parse_mode=ParseMode.HTML)
        except Exception as exc:
            log.exception("Backtest failed")
            await self._safe_edit(status, f"Backtest lỗi: {html.escape(str(exc))}", ParseMode.HTML)

    async def data_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_auth(update): return
        try:
            frame = await asyncio.to_thread(self.data.history, "VNINDEX", 10)
            last = frame.iloc[-1].time
            await update.effective_message.reply_text(f"VNINDEX: {last:%d/%m/%Y}; {len(frame)} phiên gần nhất đọc thành công.")
        except DataError as exc:
            await update.effective_message.reply_text(f"Dữ liệu lỗi: {exc}")

    async def approve(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != self.settings.admin_telegram_id:
            await update.effective_message.reply_text("Chỉ admin được dùng lệnh này.")
            return
        if not context.args or not context.args[0].isdigit():
            await update.effective_message.reply_text("Cú pháp: /approve 123456789")
            return
        self.users.approve(int(context.args[0]))
        await update.effective_message.reply_text("Đã duyệt người dùng.")

    async def detail_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not self.authorized(update):
            await query.message.reply_text("Bạn chưa được duyệt.")
            return
        _, section, symbol = query.data.split(":", 2)
        report = self.report_cache.get((query.from_user.id, symbol))
        if not report:
            await query.message.reply_text(f"Báo cáo {symbol} đã hết phiên nhớ; hãy chạy /analyze {symbol} lại.")
            return
        await query.message.reply_text(report[section][:4096], parse_mode=ParseMode.HTML)

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Keep transient Telegram transport errors from being reported as model/data failures."""
        error = context.error
        if isinstance(error, (NetworkError, TimedOut, RetryAfter)):
            log.warning("Transient Telegram transport error outside delivery wrapper: %s",
                        type(error).__name__)
            return
        log.error("Unhandled Telegram update error", exc_info=error)

    def application(self) -> Application:
        app = (Application.builder().token(self.settings.telegram_bot_token)
               .connect_timeout(20).read_timeout(30).write_timeout(30).pool_timeout(20)
               .concurrent_updates(4).post_init(self.post_init).build())
        for name, handler in [("start", self.start), ("help", self.start), ("market", self.market_command), ("analyze", self.analyze_command),
                              ("compare", self.compare_command),
                              ("technical", self.technical_command), ("fundamental", self.fundamental_command),
                              ("method", self.method_command), ("strategy", self.method_command), ("live", self.live_command),
                              ("screen", self.screen_command), ("signals", self.screen_command),
                              ("forecast", self.market_command),
                              ("sector", self.sector_command),
                              ("backtest", self.backtest_command), ("position", self.position_command),
                              ("portfolio", self.portfolio_command), ("risk", self.position_command),
                              ("data_status", self.data_status), ("approve", self.approve)]:
            app.add_handler(CommandHandler(name, handler))
        app.add_handler(CallbackQueryHandler(
            self.menu_callback,
            pattern=r"^menu:(market|signals|analyze_help|sector_help|backtest_help|portfolio_help|strategy|help)$"))
        app.add_handler(CallbackQueryHandler(self.detail_callback, pattern=r"^detail:(technical|fundamental|strategy):[A-Z0-9]{2,10}$"))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.agent_message))
        app.add_error_handler(self.error_handler)
        return app
