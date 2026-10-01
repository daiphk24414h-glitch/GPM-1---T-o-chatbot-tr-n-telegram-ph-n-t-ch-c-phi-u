# Nâng cấp screening và tín hiệu — V6.1

## Tốc độ và độ bền

- Lưu OHLCV ngày xuống `runtime/market_cache`; mặc định dùng lại trong 12 giờ và kiểm tra sớm sau khi thị trường đóng cửa.
- Lưu snapshot cơ bản 12 giờ và kết quả screen 30 phút, kể cả khi bot khởi động lại.
- SSI tải đồng thời tối đa 5 mã. Nếu SSI không có dữ liệu, nhánh Vnstock Free được tuần tự hóa theo khoảng 3,2 giây để không phá hạn mức.
- Tầng cơ bản chỉ tải 5–10 mã dẫn đầu thay vì cố định 20 mã.
- Cập nhật tiến độ Telegram được giới hạn theo thời gian, retry khi mất kết nối và không được phép hủy tác vụ phân tích.
- Nếu gửi kết quả thất bại, kết quả vẫn được lưu. Yêu cầu kế tiếp có thể đọc lại mà không quét từ đầu.

## Xếp hạng peers

- Trong screen, sức mạnh tương đối kỹ thuật được ghép từ 60% sức mạnh so với VNINDEX và 40% percentile trong universe đang xét. Với `/sector`, universe chính là các peers ngành.
- Growth, Quality, Health và Valuation được chuyển sang percentile trong shortlist có dữ liệu.
- Điểm cơ bản điều chỉnh peers dùng 60% điểm tuyệt đối và 40% percentile peers.
- Quality và Health dưới ngưỡng an toàn chặn BUY, kể cả khi định giá thấp.
- Báo cáo hiển thị riêng điểm so với VNINDEX, điểm so với peers, giá xác nhận, hard stop và cỡ mẫu cơ bản.

## Yêu cầu ngôn ngữ tự nhiên

- “Các cổ phiếu có tín hiệu buy hôm nay” → quét VN100.
- “Điểm mua bán FPT hôm nay” → phân tích FPT.
- “Dự báo thị trường sắp tới” → kịch bản VNINDEX 5–20 phiên.
- `/signals` là bí danh của `/screen`; `/forecast` là bí danh của `/market`.

Tín hiệu hôm nay chỉ dùng phiên ngày đã hoàn tất. Trước 15:10 trong ngày giao dịch, nến ngày đang hình thành bị loại khỏi phép tính xác nhận.
