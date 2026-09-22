# Vietnamese Stock Analysis Telegram Bot — Quant V6.3.2

Bot Telegram phân tích cổ phiếu Việt Nam theo hai tầng: bộ lọc cơ bản và tín hiệu kỹ thuật. Backtest dùng cùng `TechnicalEngine` với phân tích hằng ngày, có phí, thuế, slippage, mark-to-market và các chỉ số CAGR, MDD, Sharpe, Sortino, win rate, profit factor.

## Trạng thái hiện tại

- Đã có Telegram long polling, whitelist SQLite, biểu đồ, phân tích kỹ thuật, quét watchlist và backtest.
- Đã chuẩn hóa tệp `data/VNINDEX.csv` với tiêu đề tiếng Việt và khối lượng K/M/B.
- Khi VNINDEX cũ hoặc không hợp lệ, bot khóa phân tích thay vì mặc định thị trường sideway.
- `/analyze` lấy tỷ số tài chính năm hiện tại từ Vnstock Free, chấm mô hình doanh nghiệp thường/ngân hàng và gửi báo cáo kỹ thuật + cơ bản + nhận định phản biện riêng.
- Bộ định tuyến hiểu câu tiếng Việt, ngữ cảnh mã gần nhất và các yêu cầu phân tích, so sánh, dữ liệu trong phiên, screen, backtest, phương pháp và quản trị vị thế. Bộ luật cục bộ tiếp quản khi dịch vụ diễn giải không sẵn sàng.
- `/screen` lấy rổ VN100 động, tải giá SSI đồng thời có giới hạn và tự chuyển Vnstock Free qua hàng đợi 3,2 giây. Giá ngày được lưu bền vững 12 giờ, kết quả screen lưu 30 phút qua lần khởi động. Tầng cơ bản chỉ tải top 5–10 tùy quy mô rổ, cache 12 giờ và chạy tối đa 3 yêu cầu song song. Lỗi Telegram khi cập nhật tiến độ được retry và không hủy tác vụ.
- Khi có SSI FastConnect Data, bot ưu tiên SSI cho thành phần chỉ số và OHLCV ngày, tự quay về Vnstock nếu SSI lỗi. `/live` hiển thị nến trong phiên 1 phút; tín hiệu giao dịch vẫn dựa trên nến ngày đã đóng.
- `/compare` và `/backtest` có nhận định phản biện sau kết quả định lượng.
- `/backtest` vẽ toàn bộ điểm BUY/SELL theo giá khớp lệnh, đánh số từng cặp giao dịch, hiển thị đường vốn và gửi kèm CSV nhật ký giao dịch đầy đủ. Tín hiệu cuối phiên được khớp ở giá mở cửa phiên kế tiếp; hard stop và trailing stop dùng mức giá mô phỏng trong engine.
- Backtest mô phỏng khối lượng nguyên từ 1 cổ phiếu để các mã thị giá cao không bị làm tròn xuống 0 khi ngân sách rủi ro nhỏ hơn một lô 100. Báo cáo tách số phiên BUY, số lệnh thực hiện và số lần bị giới hạn vốn; odd-lot thực tế có thể khớp giá khác.
- Sổ vị thế lưu riêng theo người dùng. Hard stop lấy mức chặt hơn giữa −7% và 2 ATR; trailing chỉ kích hoạt khi đồng thời đạt +1R và +5%, chỉ dời lên và thay đổi khoảng cách theo trạng thái VNINDEX.
- Worker kiểm tra vị thế định kỳ trong giờ giao dịch và chỉ gửi cảnh báo khi trạng thái chuyển sang WATCH, PROTECT hoặc EXIT.
- Báo cáo `/analyze` có hai nút Telegram mở riêng phần chi tiết kỹ thuật và cơ bản của mã vừa phân tích.
- Chuỗi xử lý gồm định tuyến → dữ liệu định lượng → nghiên cứu web → phản biện tài chính → báo cáo có nguồn. Nghiên cứu web ưu tiên công bố HOSE/HNX/UBCKNN và website doanh nghiệp, giữ URL nguồn trong đầu ra.
- Dữ liệu giá và tài chính ghi rõ Vnstock/VCI, ngày dữ liệu và giới hạn snapshot. Yêu cầu của agent được lưu trong SQLite để kiểm toán công cụ đã chọn.
- Phân loại ICB điều chỉnh trọng số Growth/Quality/Health/Valuation; ngân hàng, chứng khoán và bảo hiểm dùng taxonomy riêng. Tỷ trọng tổng hợp TA/FA vẫn giữ 80/20.
- Chứng khoán và bảo hiểm được khóa điểm cơ bản cho tới khi có mô hình chuyên ngành; không ép chung công thức doanh nghiệp sản xuất.
- Dữ liệu Free chưa có ngày công bố chuẩn point-in-time. Vì vậy dữ liệu cơ bản chỉ dùng cho phân tích hiện tại, không dùng để khẳng định kết quả backtest lịch sử.
- `/backtest` hiện dùng giả định `fundamental_pass=True` và ghi rõ giả định trong kết quả. Không dùng kết quả đó để khẳng định hiệu quả bộ lọc kép trước khi có dữ liệu cơ bản lịch sử.

## Cài đặt Windows

Hướng dẫn từng bước để cài trên máy mới, chuyển dữ liệu, vận hành trong nhóm và xử lý lỗi nằm tại [`HUONG_DAN_CAI_DAT_VA_SU_DUNG.md`](HUONG_DAN_CAI_DAT_VA_SU_DUNG.md).

Python 3.10 trở lên là bắt buộc. Môi trường mặc định của dự án nằm tại `%USERPROFILE%\.venv`.

```powershell
& "$env:USERPROFILE\.venv\Scripts\Activate.ps1"
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Điền khóa mới vào `.env`. Không dùng lại các khóa từng gửi qua chat và không commit `.env`.

```dotenv
TELEGRAM_BOT_TOKEN="token mới lấy từ BotFather"
ADMIN_TELEGRAM_ID="Telegram user ID dạng số"
VNSTOCK_API_KEY="API key tại https://vnstocks.com/account#api-key"
AI_PROVIDER="openai"
OPENAI_API_KEY="key mới; không gửi qua chat"
OPENAI_MODEL="gpt-5-mini"
DEEPSEEK_API_KEY="key mới; có thể để trống"
```

Khởi động:

```powershell
.\start.ps1
```

`start.ps1` là tiến trình máy chủ long-polling và được thiết kế để chạy liên tục; nó không tự "chạy xong" rồi trả về dấu nhắc PowerShell. Khi khởi động, bot kích hoạt `VNSTOCK_API_KEY` bằng `vnstock.core.setup_api_key` để tránh bị xếp vào hạn mức Guest.

## Tự khởi động trên Windows

Chạy `install_autostart.ps1` một lần để tạo Scheduled Task cho tài khoản Windows hiện tại. Bot chạy ẩn khi đăng nhập, tự khởi động lại sau lỗi và ghi log tại `runtime/supervisor.log`. Máy vẫn phải bật, có mạng và không sleep. Gỡ bằng `uninstall_autostart.ps1`.

Sau khi cập nhật code, chạy `restart_bot.ps1` để dừng đúng tiến trình của dự án và nạp bản mới. Nếu Windows từ chối quyền đọc tiến trình nền, mở PowerShell bằng **Run as administrator** rồi chạy lại.

Kiểm tra kết nối trước khi khởi động (không in giá trị khóa):

```powershell
& "$env:USERPROFILE\.venv\Scripts\python.exe" verify_connections.py
```

## Lệnh

- `/start` hoặc `/help` — mở bảng điều khiển có nút chọn chức năng và ví dụ bắt đầu nhanh
- `/market` — VNINDEX, thiên hướng 5–20 phiên và trọng số theo regime
- `/analyze FPT`
- `/compare FPT CMG`
- `/technical FPT`
- `/fundamental FPT`
- `/live FPT` — snapshot SSI IntradayOhlc 1 phút
- `/screen` hoặc `/screen VN100` — quét kỹ thuật toàn rổ VN100, sau đó chấm cơ bản shortlist
- `/screen VN30` hoặc `/screen watchlist`
- `/signals` — tín hiệu mua/bán theo phiên ngày đã hoàn tất gần nhất
- `/forecast` — kịch bản VNINDEX cho 5–20 phiên tới
- `/sector bán lẻ` — xếp hạng các mã thuộc ngành ICB phù hợp
- `/backtest FPT 3m`, `/backtest FPT 6m`, `/backtest FPT 1y`, `/backtest FPT 2y` — kiểm định đúng khoảng yêu cầu; cũng nhận câu tự nhiên như “backtest VIC trong 6 tháng gần đây”
- `/position add FPT 1000 120` — lưu 1.000 cổ phiếu giá vốn 120
- `/position FPT` hoặc `/risk FPT` — cập nhật trailing stop và hành động
- `/strategy` — giải thích đầy đủ chiến lược, điểm số, phân bổ vốn và quy tắc thoát
- `/position remove FPT` — đóng theo dõi
- `/portfolio` — xem danh mục
- `/method risk` — giải thích phương pháp quản trị vị thế
- `/data_status`
- `/approve 123456789` — chỉ admin

Có thể nhắn tự nhiên: `phân tích FPT`, `so sánh FPT với CMG`, `các cổ phiếu có tín hiệu buy hôm nay`, `điểm mua bán FPT hôm nay`, `dự báo thị trường sắp tới`, `trong ngành bán lẻ cổ phiếu nào dẫn đầu`.

SSI FastConnect Data là kết nối chỉ đọc. Bot không sử dụng SSI FastConnect Trading, private key, PIN/OTP hay API đặt lệnh. Cấu hình được nhận theo cả tên chuẩn `SSI_CONSUMER_ID`/`SSI_CONSUMER_SECRET` và tên cũ `CONSUMERID_SSI`/`CONSUMERSECRET_SSI`.

Trong group/supergroup, bot chỉ xử lý tin nhắn có `@username_bot` hoặc tin nhắn reply trực tiếp vào bot. `ALLOW_GROUP_USERS=true` cho phép thành viên trong các group đã thêm bot sử dụng mà không cần duyệt từng Telegram user ID; đặt `false` để quay về whitelist cá nhân.

Agent chọn công cụ, thu thập bằng chứng và viết nhận định có điều kiện. Công thức chấm điểm, tín hiệu, stop-loss và sizing nằm trong code định lượng và không cho mô hình ngôn ngữ tự sửa.

Báo cáo cơ bản hiển thị riêng kỳ tài chính gần nhất và ngày truy xuất. Dữ liệu Free không có đầy đủ ngày công bố `published_at`, nên không được dùng để giả lập rằng thị trường đã biết số liệu trước ngày công bố thực tế.

Technical scoring ranks relative strength versus VNINDEX, EMA20/50/200 trend, entry quality, volume and volatility/liquidity. The full score remains 80% technical and 20% fundamental. Backtest executes an end-of-day signal at the next session open and checks the existing stop against the next bar before calculating a new trailing level.

Chiến lược mặc định là long-only momentum–quality dành cho thị trường Việt Nam. Phần kỹ thuật đóng góp 80 điểm: sức mạnh tương đối 25, xu hướng 20, chất lượng điểm vào 15, khối lượng 10, biến động/thanh khoản 10. Trong `/sector`, sức mạnh tương đối được chia thành 15 điểm so với VNINDEX và 10 điểm so với peers. Điểm cơ bản peer-adjusted giữ 60% đánh giá tuyệt đối và 40% percentile của Growth/Quality/Health/Valuation trong shortlist; quality gate ngăn cổ phiếu chất lượng yếu đứng đầu chỉ vì định giá thấp. VNINDEX quyết định trần tỷ trọng cổ phiếu: UP mạnh 80–100%, UP yếu 60–75%, SIDEWAY 35–60%, DOWN 0–25%; kết quả `/screen` còn hạ trần nếu độ rộng thị trường dưới EMA50 yếu.

Quản trị vốn dùng 0,6% tổng tài sản rủi ro cho mỗi lệnh, tối đa 15% vốn cho một mã, danh mục mục tiêu 6–8 mã và trần ngành 25–30%. Hai giới hạn sau là nguyên tắc xây dựng danh mục; bot chưa tự đặt lệnh hay tự cân bằng tài khoản môi giới.

Để tránh mua–bán liên tục do nhiễu một phiên, tín hiệu SELL cần tối thiểu 3 phiên nắm giữ và 2 lần xác nhận trong nhánh thoát theo tín hiệu. Khi vị thế đồng thời đạt +1R và +5%, SELL chuyển sang siết trailing stop; hard stop vẫn được thực hiện ngay khi giá chạm mức mô phỏng.

Người chưa được duyệt sẽ nhận Telegram user ID của họ để gửi admin.

## Snapshot cơ bản chuẩn hóa

Adapter dữ liệu cơ bản cần trả snapshot theo ngày công bố thực tế, không phải ngày kết thúc kỳ kế toán:

```python
{
  "as_of": date(2026, 8, 15),
  "source": "provider + report identifiers",
  "revenue_cagr": 0.16,
  "eps_cagr": 0.18,
  "industry_growth": 0.10,
  "gdp_growth": 0.07,
  "roe_peer_rel": 1.15,
  "roic_peer_rel": 1.10,
  "operating_margin": 0.17,
  "de_peer_rel": 0.75,
  "interest_coverage": 5.2,
  "cfo_ni": 1.05,
  "pe_peer_rel": 0.85,
  "pb_peer_rel": 0.90,
  "ev_ebitda_peer_rel": 0.88,
}
```

Ngân hàng, chứng khoán và bảo hiểm cần schema/chấm điểm riêng; không dùng D/E, CFO/NI và EV/EBITDA của doanh nghiệp công nghiệp một cách máy móc.

## Kiểm thử

```powershell
python -m pytest -q
```

## Trước khi triển khai thật

1. Nối nguồn dữ liệu cơ bản có lịch sử ngày công bố và peer universe.
2. Thay giả định FS trong backtest bằng snapshot có hiệu lực tại từng ngày.
3. Thêm benchmark alpha/beta và backtest cấp danh mục nhiều mã.
4. Chạy walk-forward, out-of-sample và paper trading.
5. Khi nhiều người dùng, chuyển sang webhook HTTPS, PostgreSQL và worker queue.
