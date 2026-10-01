from __future__ import annotations

import html


PRICE_DOC = "https://www.vnstocks.com/docs/vnstock-data/data-sources"
FUND_DOC = "https://www.vnstocks.com/docs/vnstock-data/fundamental-layer-v3"


def technical_report(tech) -> str:
    i = tech.indicators
    ema20, ema50, ema200 = i.get("ema20"), i.get("ema50"), i.get("ema200")
    rsi, rvol = i.get("rsi14"), i.get("rvol20")
    lower, upper, atr = i.get("bb_lower"), i.get("bb_upper"), i.get("atr14")
    trend = "tăng" if ema20 and ema50 and ema20 > ema50 else "giảm/yếu"
    momentum = "tích cực nhưng chưa quá mua" if rsi is not None and 50 <= rsi < 70 else (
        "quá nóng, rủi ro rung lắc" if rsi is not None and rsi >= 70 else
        "yếu, chưa xác nhận lực cầu" if rsi is not None and rsi < 40 else "trung tính")
    liquidity = "dòng tiền xác nhận" if rvol is not None and rvol >= 1.5 else (
        "thanh khoản đạt mức bình thường" if rvol is not None and rvol >= 1 else "dòng tiền chưa xác nhận")
    atr_pct = atr / tech.price * 100 if atr and tech.price else 0
    rr = ((tech.take_profit - tech.price) / (tech.price - tech.stop_loss)
          if tech.take_profit and tech.stop_loss and tech.price > tech.stop_loss else 0)
    c = tech.components
    factors = (f"RS so với VNINDEX {c.get('relative_strength', 0):.1f} · Xu hướng {c.get('trend', 0):.1f} · "
               f"Điểm vào {c.get('entry_quality', 0):.1f} · Khối lượng {c.get('volume', 0):.1f} · "
               f"Rủi ro/thanh khoản {c.get('risk_liquidity', 0):.1f}")
    return (
        f"📈 <b>PHÂN TÍCH KỸ THUẬT — {html.escape(tech.symbol)}</b>\n\n"
        f"<b>Cấu trúc xu hướng</b>\n"
        f"• EMA20/EMA50/EMA200: {ema20 or 0:,.2f} / {ema50 or 0:,.2f} / {ema200 or 0:,.2f}; cấu trúc đang {trend}.\n"
        f"• VNINDEX: {tech.regime.value}; trọng số chỉ báo đã được điều chỉnh theo trạng thái này.\n\n"
        f"<b>Điểm thành phần</b>\n• {factors}.\n"
        "• Trong điểm tổng hợp: RS chiếm 25%, xu hướng 20%, điểm vào 15%, khối lượng 10%, "
        "rủi ro/thanh khoản 10% và cơ bản 20%.\n\n"
        f"<b>Động lượng và dòng tiền</b>\n"
        f"• RSI(14): {rsi or 0:.1f} — {momentum}.\n"
        f"• RVOL(20): {rvol or 0:.2f}x — {liquidity}.\n"
        f"• Bollinger: {lower or 0:,.2f}–{upper or 0:,.2f}; dùng để xác nhận breakout hoặc hồi quy trong sideway.\n\n"
        f"<b>Quản trị vị thế</b>\n"
        f"• Giá tham chiếu: {tech.price:,.2f}; hard stop {tech.stop_loss or 0:,.2f}; mốc lợi nhuận 2R {tech.take_profit or 0:,.2f}.\n"
        "• Mốc 2R là mục tiêu tham chiếu, không phải lệnh chốt cứng. Trailing chỉ bật khi đồng thời đạt +1R và +5%.\n"
        f"• ATR(14): {atr or 0:,.2f} ({atr_pct:.1f}% giá); tỷ lệ reward/risk thiết kế khoảng {rr:.1f}:1.\n"
        f"• Quy mô tối đa theo mô hình: {tech.recommended_shares:,} cổ phiếu; rủi ro 0,6% vốn/lệnh và tối đa 15% vốn/mã.\n\n"
        f"<b>Luận điểm</b>\n• {html.escape(' '.join(tech.reasons))}\n"
        f"• Tín hiệu mất hiệu lực khi giá vi phạm stop-loss hoặc cấu trúc VNINDEX chuyển xấu.\n\n"
        f"<b>Nguồn</b>: <a href=\"{PRICE_DOC}\">Vnstock Quote/VCI</a>, dữ liệu ngày đến {tech.as_of:%d/%m/%Y}; "
        "chỉ báo do hệ thống tính từ OHLCV."
    )


def fundamental_report(fund) -> str:
    labels = {"pe_ratio": "P/E", "pb_ratio": "P/B", "ev_to_ebitda": "EV/EBITDA", "roe": "ROE", "roa": "ROA",
              "roic": "ROIC", "ebit_margin": "Biên EBIT", "debt_to_equity": "D/E",
              "current_ratio": "Thanh toán hiện hành", "eps_cagr": "EPS CAGR", "net_interest_margin": "NIM",
              "npl": "Nợ xấu", "car": "CAR", "loans_growth": "Tăng trưởng tín dụng",
              "deposit_growth": "Tăng trưởng tiền gửi", "financial_leverage": "Đòn bẩy tài chính"}
    pct = {"roe", "roa", "roic", "ebit_margin", "eps_cagr", "net_interest_margin", "npl", "car",
           "loans_growth", "deposit_growth"}
    lines = []
    for key, label in labels.items():
        value = fund.metrics.get(key)
        if isinstance(value, (int, float)):
            lines.append(f"• {label}: {value:.1%}" if key in pct else f"• {label}: {value:.2f}")
    components = " · ".join(f"{k.title()} {v:.1f}" for k, v in fund.components.items() if isinstance(v, (int, float)))
    coverage = "Đủ cho sàng lọc hiện tại" if fund.score is not None else "Chưa đủ để chấm điểm đáng tin cậy"
    industry = fund.metrics.get("industry") or "Chưa phân loại"
    company_type = fund.metrics.get("company_type") or "CT"
    model_weights = fund.metrics.get("model_weights")
    period = fund.metrics.get("financial_period") or "Không xác định"
    retrieved = fund.metrics.get("retrieved_at") or (fund.as_of.isoformat() if fund.as_of else "Không xác định")
    m = fund.metrics
    analysis = []

    eps = m.get("eps_cagr")
    if isinstance(eps, (int, float)):
        if eps >= .15:
            analysis.append(f"• EPS tăng trưởng kép {eps:.1%}, cho thấy năng lực mở rộng lợi nhuận đang ở mức tốt.")
        elif eps >= 0:
            analysis.append(f"• EPS tăng trưởng kép {eps:.1%}, doanh nghiệp vẫn tăng lợi nhuận nhưng tốc độ chưa nổi bật.")
        else:
            analysis.append(f"• EPS CAGR {eps:.1%}, lợi nhuận trên cổ phần đang co lại và làm suy yếu luận điểm tăng trưởng.")
    roe = m.get("roe")
    if isinstance(roe, (int, float)):
        assessment = "hiệu quả vốn chủ tốt" if roe >= .18 else "mức chấp nhận được" if roe >= .10 else "hiệu quả vốn chủ thấp"
        analysis.append(f"• ROE {roe:.1%}: {assessment}; cần đọc cùng đòn bẩy để tránh nhầm hiệu quả cao do vay nợ.")
    roic = m.get("roic")
    if isinstance(roic, (int, float)):
        assessment = "khả năng phân bổ vốn tích cực" if roic >= .12 else "hiệu quả vốn đầu tư trung bình" if roic >= .07 else "hiệu quả sử dụng vốn yếu"
        analysis.append(f"• ROIC {roic:.1%}: {assessment}.")
    margin = m.get("ebit_margin")
    if isinstance(margin, (int, float)):
        assessment = "biên hoạt động khỏe" if margin >= .15 else "biên hoạt động vừa phải" if margin >= .07 else "biên mỏng, nhạy với biến động chi phí"
        analysis.append(f"• Biên EBIT {margin:.1%}: {assessment}.")
    debt = m.get("debt_to_equity")
    if isinstance(debt, (int, float)) and company_type == "CT":
        assessment = "đòn bẩy được kiểm soát" if debt <= .8 else "đòn bẩy cần theo dõi" if debt <= 1.5 else "đòn bẩy cao, làm tăng rủi ro chu kỳ"
        analysis.append(f"• D/E {debt:.2f}: {assessment}.")
    current = m.get("current_ratio")
    if isinstance(current, (int, float)) and company_type == "CT":
        assessment = "vùng đệm vốn lưu động tương đối tốt" if current >= 1.5 else "khả năng thanh toán ngắn hạn vừa đủ" if current >= 1 else "rủi ro thanh khoản ngắn hạn cần kiểm tra sâu"
        analysis.append(f"• Current ratio {current:.2f}: {assessment}.")
    nim, npl, car = m.get("net_interest_margin"), m.get("npl"), m.get("car")
    if isinstance(nim, (int, float)):
        analysis.append(f"• NIM {nim:.1%}: " + ("biên lãi thuần tốt." if nim >= .04 else "biên lãi thuần chưa cao, cần theo dõi chi phí vốn."))
    if isinstance(npl, (int, float)):
        analysis.append(f"• NPL {npl:.1%}: " + ("chất lượng tài sản đang trong vùng kiểm soát." if npl < .02 else "rủi ro chất lượng tài sản cần được ưu tiên."))
    if isinstance(car, (int, float)):
        analysis.append(f"• CAR {car:.1%}: " + ("có vùng đệm vốn tương đối." if car >= .10 else "vùng đệm vốn còn mỏng."))
    pe, pb, ev = m.get("pe_ratio"), m.get("pb_ratio"), m.get("ev_to_ebitda")
    valuation_parts = []
    if isinstance(pe, (int, float)): valuation_parts.append(f"P/E {pe:.2f}")
    if isinstance(pb, (int, float)): valuation_parts.append(f"P/B {pb:.2f}")
    if isinstance(ev, (int, float)): valuation_parts.append(f"EV/EBITDA {ev:.2f}")
    if valuation_parts:
        analysis.append("• Định giá hiện tại: " + ", ".join(valuation_parts) + ". Chưa thể kết luận rẻ/đắt nếu thiếu trung vị peer cùng ngành và giai đoạn chu kỳ.")
    scored_components = {k: v for k, v in fund.components.items() if isinstance(v, (int, float))}
    if scored_components:
        strongest = max(scored_components, key=scored_components.get)
        weakest = min(scored_components, key=scored_components.get)
        analysis.append(f"• Thành phần mạnh nhất là {strongest} ({scored_components[strongest]:.1f}); điểm nghẽn là {weakest} ({scored_components[weakest]:.1f}).")
    return (
        f"🏢 <b>PHÂN TÍCH CƠ BẢN — {html.escape(fund.symbol)}</b>\n\n"
        f"<b>Kết quả</b>\n• Kỳ tài chính gần nhất: {html.escape(str(period))}; dữ liệu được lấy ngày {html.escape(str(retrieved))}.\n"
        f"• Ngành ICB: {html.escape(str(industry))}.\n"
        f"• Taxonomy báo cáo: {html.escape(str(company_type))}.\n"
        f"• Trạng thái: {html.escape(fund.status)}; điểm: {'N/A' if fund.score is None else f'{fund.score:.1f}/100'}.\n"
        f"• Độ phủ: {coverage}.\n"
        + (f"• Trọng số mô hình ngành: {html.escape(str(model_weights))}.\n" if model_weights else "")
        + (f"• Thành phần: {html.escape(components)}.\n" if components else "") + "\n"
        f"<b>Chỉ tiêu đầu vào</b>\n" + ("\n".join(lines) if lines else "• Không có đủ tỷ số đã chuẩn hóa.") + "\n\n"
        f"<b>Nhận định cơ bản</b>\n" + ("\n".join(analysis) if analysis else "• Độ phủ chỉ tiêu chưa đủ để hình thành luận điểm cơ bản.") + "\n"
        + (f"• Dữ liệu còn thiếu: {html.escape(', '.join(fund.missing))}.\n" if fund.missing else "") + "\n"
        f"<b>Nguồn</b>: <a href=\"{FUND_DOC}\">Vnstock Fundamental/VCI</a>. {html.escape(fund.source)}.\n"
        "<i>Kỳ báo cáo không đồng nghĩa ngày công bố. Snapshot Free hiện không cung cấp đầy đủ published_at; vì vậy không dùng dữ liệu này làm bằng chứng backtest point-in-time.</i>"
    )


def research_sources(result) -> str:
    if result.status != "OK":
        return "<b>Thông tin sự kiện</b>\n• Phạm vi hiện tại chỉ gồm dữ liệu thị trường và báo cáo tài chính đã nêu nguồn."
    links = []
    for source in result.sources:
        links.append(f"• <a href=\"{html.escape(source['url'], quote=True)}\">{html.escape(source['title'])}</a>")
    return ("<b>Thông tin sự kiện 90 ngày</b>\n" + html.escape(result.summary) +
            ("\n\n<b>Nguồn tham khảo</b>\n" + "\n".join(links) if links else ""))
