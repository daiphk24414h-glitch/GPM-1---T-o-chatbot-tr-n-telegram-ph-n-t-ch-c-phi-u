# Hướng dẫn cài đặt và sử dụng VNStock Telegram Bot — Quant V6.3.2

Tài liệu này dùng khi cài mới, chuyển bot sang máy Windows khác hoặc hướng dẫn thành viên sử dụng. Gói cài đặt không chứa `.env`, token hay API key.

## 1. Bot làm được gì?

- Đánh giá xu hướng VNINDEX và xây dựng kịch bản 5–20 phiên.
- Phân tích kỹ thuật và cơ bản từng cổ phiếu, kèm nguồn và thời điểm dữ liệu.
- Quét VN100, tìm tín hiệu theo phiên ngày gần nhất và xếp hạng theo ngành.
- So sánh nhiều cổ phiếu cùng hệ tiêu chí.
- Backtest theo 3 tháng, 6 tháng, 1 năm, 2 năm hoặc khoảng dài hơn; biểu đồ thể hiện điểm mua/bán và nhật ký giao dịch.
- Lưu vị thế để theo dõi hard stop, trailing stop và trạng thái quản trị rủi ro.
- Nhận câu hỏi tiếng Việt tự nhiên trong tin nhắn riêng hoặc khi được tag trong nhóm.

Bot chỉ hỗ trợ phân tích và quản trị quyết định. Bot không tự đặt lệnh, không dùng PIN/OTP và không truy cập tài khoản giao dịch SSI.

## 2. Chuẩn bị máy mới

Máy cần:

- Windows 10 hoặc Windows 11.
- Python 3.10 trở lên.
- PowerShell và kết nối Internet ổn định.
- Token Telegram Bot từ BotFather.
- Telegram user ID của quản trị viên.
- API key Vnstock; cấu hình SSI và OpenAI là tùy chọn nhưng giúp mở rộng dữ liệu và phần diễn giải.

Không chạy cùng một token Telegram trên hai máy. Telegram chỉ cho phép một tiến trình long polling; nếu máy cũ và máy mới cùng chạy, bot sẽ báo `Conflict: terminated by other getUpdates request`.

## 3. Cài đặt lần đầu

Giải nén gói vào một thư mục cố định, ví dụ `C:\VNStockTelegramBot`. Mở PowerShell và chạy:

```powershell
cd "C:\VNStockTelegramBot"
py --version
py -m venv "$env:USERPROFILE\.venv"
& "$env:USERPROFILE\.venv\Scripts\Activate.ps1"
python -m pip install -U pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Nếu lệnh `py` không tồn tại, cài Python từ trang chính thức, chọn tùy chọn thêm Python vào PATH rồi mở lại PowerShell.

## 4. Cấu hình `.env`

Điền giá trị riêng của bạn vào `.env`. Các trường chính:

```dotenv
TELEGRAM_BOT_TOKEN="token từ BotFather"
ADMIN_TELEGRAM_ID="Telegram user ID dạng số"
VNSTOCK_API_KEY="API key Vnstock"

AI_PROVIDER="openai"
OPENAI_API_KEY="API key OpenAI; có thể để trống"
OPENAI_MODEL="gpt-5-mini"

SSI_CONSUMER_ID="có thể để trống"
SSI_CONSUMER_SECRET="có thể để trống"
SSI_PUBLIC_KEY="có thể để trống"
SSI_API_KEY="có thể để trống"

ALLOW_GROUP_USERS="true"
```

Tên biến SSI thực tế phải theo `.env.example` đi kèm phiên bản hiện tại. Không gửi `.env` qua nhóm chat, không đưa vào Git và không chụp màn hình có chứa khóa. Nếu khóa từng xuất hiện công khai, hãy thu hồi và tạo khóa mới.

## 5. Kiểm tra kết nối

```powershell
& "$env:USERPROFILE\.venv\Scripts\python.exe" verify_connections.py
```

Kết quả tối thiểu cần có:

- `TELEGRAM: OK`: bot kết nối được Telegram.
- `VNSTOCK: OK`: nguồn dữ liệu Vnstock hoạt động.
- `SSI DATA: OK`: nếu đã cấu hình SSI.
- `OPENAI: OK`: nếu dùng phần nhận định mở rộng.

Nếu OpenAI lỗi, phần tính điểm, tín hiệu và backtest vẫn chạy bằng engine định lượng; phần diễn giải mở rộng sẽ ngắn hơn. Telegram lỗi thì bot không thể nhận hoặc gửi tin nhắn.

## 6. Chạy thử và bật tự khởi động

Chạy trực tiếp để kiểm tra lần đầu:

```powershell
.\start.ps1
```

Cửa sổ này cần mở trong lúc thử. Gửi `/start` cho bot trên Telegram. Khi bot hoạt động đúng, nhấn `Ctrl+C` để dừng rồi cài chế độ tự khởi động:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1
```

Bot sẽ chạy ẩn khi đăng nhập Windows và tự khởi động lại nếu tiến trình gặp lỗi. Máy phải bật, có mạng và không ở chế độ sleep.

Sau mỗi lần cập nhật mã nguồn, mở PowerShell bằng **Run as administrator** rồi chạy:

```powershell
cd "C:\VNStockTelegramBot"
powershell -ExecutionPolicy Bypass -File .\restart_bot.ps1
```

Gỡ chế độ tự khởi động:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_autostart.ps1
```

## 7. Cách sử dụng nhanh

Gửi `/start` để mở bảng điều khiển. Người dùng có thể bấm nút hoặc nhắn tự nhiên.

| Nhu cầu | Lệnh hoặc câu mẫu |
|---|---|
| Xu hướng thị trường | `/market` hoặc `dự báo thị trường sắp tới` |
| Phân tích một mã | `/analyze FPT` hoặc `phân tích FPT` |
| Phân tích kỹ thuật | `/technical FPT` |
| Phân tích cơ bản | `/fundamental FPT` |
| So sánh | `/compare FPT CMG` hoặc `so sánh FPT với CMG` |
| Dữ liệu trong phiên | `/live FPT` |
| Quét VN100 | `/screen` hoặc `cổ phiếu có tín hiệu mua hôm nay` |
| Xếp hạng ngành | `/sector ngân hàng` hoặc `trong ngành ngân hàng mã nào dẫn đầu` |
| Backtest 3 tháng | `/backtest FPT 3m` |
| Backtest 6 tháng | `/backtest FPT 6m` |
| Xem chiến lược | `/strategy` |
| Ghi nhận vị thế | `/position add FPT 1000 120` |
| Kiểm tra vị thế | `/position FPT` |
| Xem danh mục | `/portfolio` |
| Kiểm tra dữ liệu | `/data_status` |

Trong nhóm, tag username của bot, ví dụ `@VNStockAnalysisBot phân tích FPT`, hoặc reply trực tiếp vào tin nhắn của bot. `ALLOW_GROUP_USERS=true` cho phép thành viên trong nhóm dùng bot. Với BotFather Privacy Mode đang bật, bot vẫn nhận lệnh, tin nhắn được tag và tin nhắn reply; đây là cấu hình phù hợp cho hầu hết nhóm.

## 8. Phương pháp phân tích

Điểm tổng hợp mặc định gồm 80% kỹ thuật và 20% cơ bản. Phần kỹ thuật xem sức mạnh tương đối so với VNINDEX và peers, xu hướng EMA20/50/200, chất lượng điểm vào, khối lượng, biến động và thanh khoản. Phần cơ bản đánh giá tăng trưởng, chất lượng sinh lời, sức khỏe tài chính và định giá; công thức được điều chỉnh theo ngành và có quality gate để tránh chọn doanh nghiệp yếu chỉ vì chỉ số định giá thấp.

VNINDEX quyết định chế độ thị trường và trần tỷ trọng cổ phiếu. Sideway ưu tiên tín hiệu dải giá và điểm vào; xu hướng tăng ưu tiên momentum và sức mạnh tương đối; xu hướng giảm siết điều kiện, giảm tỷ trọng và đề cao bảo toàn vốn.

Chiến lược là long-only momentum–quality. Rủi ro đề xuất mặc định là 0,6% tài sản cho mỗi lệnh, tối đa 15% vốn cho một mã, danh mục mục tiêu 6–8 mã và giới hạn ngành 25–30%. Hard stop lấy mức chặt hơn giữa −7% và 2 ATR. Khi vị thế đồng thời đạt +1R và +5%, trailing stop được kích hoạt và chỉ dời lên.

Backtest dùng chính engine tín hiệu đang dùng trong phân tích hằng ngày. Tín hiệu cuối phiên được khớp ở giá mở cửa phiên tiếp theo, có phí, thuế và slippage. Kết quả gồm CAGR, drawdown, Sharpe, Sortino, win rate, profit factor, so sánh VNINDEX, biểu đồ điểm BUY/SELL và file CSV nhật ký lệnh.

Khối lượng backtest được làm tròn theo đơn vị một cổ phiếu để mô phỏng odd-lot. Cách này giữ đúng giới hạn 0,6% rủi ro và 15% vốn cho các mã thị giá cao; lệnh odd-lot ngoài thực tế có thể có thanh khoản và giá khớp kém hơn dữ liệu nến ngày.

Giới hạn cần hiểu rõ: dữ liệu Vnstock Free chưa có lịch sử ngày công bố báo cáo đầy đủ theo chuẩn point-in-time. Vì thế backtest hiện kiểm tra chiến lược kỹ thuật và giả định bộ lọc cơ bản đạt yêu cầu; không được diễn giải kết quả đó như backtest hoàn chỉnh của chiến lược kỹ thuật–cơ bản.

## 9. Dữ liệu và thư mục vận hành

- `runtime/bot.db`: người dùng được duyệt, vị thế và lịch sử điều phối yêu cầu.
- `runtime/supervisor.log`: log tiến trình chạy nền.
- `runtime/market_cache`: cache giá thị trường.
- `runtime/fundamental_cache`: cache dữ liệu cơ bản.
- `runtime/screen_cache.json`: kết quả quét gần nhất.
- `outputs`: biểu đồ và file kết quả tạm thời.

Lần quét VN100 đầu tiên có thể lâu vì bot phải tải dữ liệu. Các lần sau nhanh hơn nhờ cache. Kết quả cơ bản ghi rõ kỳ tài chính gần nhất và ngày truy xuất; dữ liệu trong phiên SSI được ghi riêng, không trộn thành nến ngày đã hoàn tất.

## 10. Chuyển bot sang máy khác

1. Dừng bot trên máy cũ hoặc gỡ autostart để tránh lỗi Telegram Conflict.
2. Chép file ZIP phiên bản mới sang máy mới và giải nén.
3. Cài Python, môi trường và thư viện theo mục 3.
4. Tạo lại `.env` trên máy mới hoặc chuyển qua một kênh bảo mật.
5. Nếu cần giữ danh sách được duyệt và vị thế, chép `runtime/bot.db` khi bot trên máy cũ đã dừng.
6. Không chép file PID, lock hay log cũ trong `runtime`.
7. Chạy `verify_connections.py`, chạy thử bằng `start.ps1`, rồi cài autostart.

Cache có thể chép nhưng không bắt buộc. Xóa cache không làm mất cấu hình hay vị thế; bot sẽ tải lại dữ liệu.

## 11. Xử lý lỗi thường gặp

### Gửi `/start` nhưng bot im lặng

Mở `runtime\supervisor.log`. Chạy `verify_connections.py`, sau đó mở PowerShell bằng quyền Administrator và chạy `restart_bot.ps1`. Kiểm tra máy có mạng và không sleep.

### Telegram báo `Conflict`

Cùng token đang chạy ở hai tiến trình hoặc hai máy. Dừng bản cũ, gỡ autostart ở máy không dùng rồi restart đúng một bản.

### Bot vẫn hiện giao diện phiên bản cũ

Tiến trình cũ chưa dừng. Chạy `restart_bot.ps1` bằng PowerShell Administrator, đợi khoảng 15 giây rồi gửi `/start` mới.

### `httpx.ConnectError` hoặc lỗi gửi tiến độ

Đây thường là kết nối mạng tới Telegram hoặc nguồn dữ liệu. Bot có retry và cache, nhưng mất mạng kéo dài vẫn cần khôi phục kết nối rồi chạy lại yêu cầu.

### OpenAI `HTTP 429`

Tài khoản đã hết quota hoặc chạm giới hạn tốc độ. Kiểm tra billing/quota. Tạo key mới trong cùng tài khoản hết quota không giải quyết được; cần bổ sung hạn mức hoặc dùng tài khoản có quota.

### Vnstock báo giới hạn request

Đợi cửa sổ giới hạn được đặt lại. Không chạy nhiều `/screen` song song. Cấu hình API key Vnstock hợp lệ giúp bot dùng đúng tier; cache của bot giảm số lượt tải lặp.

### `/screen` dừng ở 97/97

Sau tầng kỹ thuật, bot còn xếp hạng và có thể tải dữ liệu cơ bản cho shortlist. Phiên bản hiện tại giới hạn concurrency, dùng cache và có cơ chế gửi kết quả dự phòng. Nếu quá lâu, xem `supervisor.log` để biết Telegram hay nguồn dữ liệu nào đang lỗi.

## 12. Kiểm thử bản cài

```powershell
& "$env:USERPROFILE\.venv\Scripts\python.exe" -m pytest -q
```

Chỉ dùng bản mới khi kiểm thử hoàn tất và `verify_connections.py` cho thấy Telegram cùng ít nhất một nguồn giá hoạt động.
