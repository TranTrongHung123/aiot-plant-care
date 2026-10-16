# Frontend PlantCare — tổ chức màn hình

## Rà soát và sitemap

Trước thay đổi, một trang Tổng quan chứa cả thông tin cây, cảm biến, thời tiết,
điều khiển bơm, cấu hình lịch, mô phỏng và lịch sử. Ba tab nội bộ không có URL
riêng; menu trên điện thoại mất nhãn, thao tác chuyên sâu lẫn với giám sát.

Sitemap mới phân chia theo nghiệp vụ:

| URL | Trang | Nội dung |
| --- | --- | --- |
| `/` | Tổng quan | Tình trạng đất/lá, cảm biến, lịch tiếp theo, cảnh báo, kết nối thiết bị |
| `/schedule` | Lịch tưới | Lịch hằng ngày/chu kỳ, ngưỡng, thời lượng, cooldown, nguồn thời tiết DEMO/LIVE |
| `/control` | Điều khiển tưới | Gửi lệnh thủ công, trạng thái relay và diễn biến độ ẩm đất |
| `/ai` | Phân tích AI | Upload lá/quả, kết quả model, yêu cầu dự báo mưa, lịch sử tác vụ |
| `/history` | Lịch sử tưới | Lệnh, nguồn, trạng thái và chuỗi sự kiện ACK |
| `/simulation` | Mô phỏng | Kịch bản normal/dry/rain/offline |

Tổng quan chỉ đọc dữ liệu, có liên kết tới trang xử lý tương ứng. Không có form
cấu hình hay nút gửi lệnh bơm trên Tổng quan. Nhóm route `(dashboard)` giữ layout,
phiên đăng nhập và WebSocket khi chuyển trang. URL hỗ trợ Back/Forward và mở trực
tiếp; reload vẫn yêu cầu đăng nhập lại như trước. Token chỉ nằm trong bộ nhớ.

## Cấu trúc code

- `frontend/src/components/plantcare-app.tsx`: phiên đăng nhập, snapshot REST/WS,
  trạng thái thao tác và các callback API hiện có.
- `plantcare-context.tsx`: hợp đồng trạng thái dùng chung giữa các trang.
- `app-shell.tsx`: Sidebar, thông tin kết nối, tiêu đề, menu mobile bằng native
  dialog, focus bàn phím, skip link và đăng xuất.
- `overview.tsx`: chỉ diễn giải dữ liệu backend để trình bày tổng quan.
- `views/`: component riêng cho từng nghiệp vụ. Các file `app/(dashboard)/*/page.tsx`
  chọn view tương ứng, không giữ toàn bộ chức năng trong một page.
- `dashboard.tsx`, `weather-panel.tsx`: tái sử dụng biểu đồ, ảnh private, form
  quy tắc, biểu tượng và cấu hình thời tiết.
- `lib/overview.ts`: tính mốc lịch kế tiếp để hiển thị; không phát lệnh.
- `app/globals.css`: token màu, khoảng cách, typography, surface, trạng thái,
  breakpoint và các component. Không thêm dependency giao diện.

## Nguyên tắc dữ liệu và giao diện

- Màu xanh trầm, nền sáng, nền phụ có sắc xanh. Màu cảnh báo/lỗi chỉ dùng cho
  trạng thái có ý nghĩa. Số liệu dùng tabular figures.
- Sidebar có nhãn đầy đủ trên desktop; dưới 1000 px dùng drawer có đóng bằng
  Escape, quản lý focus của native dialog và nút mở tối thiểu 44 px.
- Bốn cảm biến thành hai cột trên điện thoại. Bảng lịch sử cuộn trong vùng bảng,
  không làm tràn toàn trang. Hỗ trợ `prefers-reduced-motion`.
- Không thêm dữ liệu giả, không đổi adapter AI, không sửa logic điều khiển bơm.
  Trạng thái mô phỏng và Mock lịch sử vẫn có nhãn rõ ràng.
- Độ ẩm thiếu dùng dấu `—`; không suy ra phần trăm từ raw. Tình trạng đất dùng
  ngưỡng policy thật; không suy diễn cây khỏe từ cảm biến.
- Kết quả lá gần nhất có ngày, giờ, nguồn và chú thích đây là kết quả ảnh đã
  phân tích, không phải đánh giá sức khỏe hiện tại.
- Mốc lịch dự kiến tính từ `server_time`, `schedule_started_at`, kiểu lịch và
  giờ Việt Nam. Backend vẫn kiểm tra mọi điều kiện ở mỗi lượt; mốc dự kiến không
  phải cam kết bơm sẽ bật. Lịch tắt hiển thị “Chưa bật lịch”.
- Cấu hình DEMO/LIVE tập trung ở Lịch tưới; Phân tích AI chỉ dẫn đến cấu hình,
  hiển thị nguồn đang dùng và cho phép yêu cầu job mưa với điều kiện sẵn có.
- Lịch sử AI phân trang 6 tác vụ, nhóm theo ngày và lọc loại phân tích trên tập
  tối đa 30 tác vụ backend trả về. Không bỏ dữ liệu lịch sử.

## Áp dụng skill và kiểm tra giao diện

Đã đọc và áp dụng `redesign-existing-projects` và `design-taste-frontend` theo
yêu cầu. Skill thứ hai ghi rõ không dành cho dashboard/app screen: áp dụng
nguyên tắc phân cấp, màu sắc, tính trung thực của nội dung và vòng xem ảnh/sửa;
không áp dụng quota ảnh, hero marketing hay animation cho màn hình vận hành.
Không tạo mockup, không tải ảnh stock để giả ảnh cây.

Vòng xem ảnh đầu tiên (1440, 768, 390 px): điều hướng và bố cục không tràn ngang.
Ba điểm cải thiện: thiếu thời điểm đo trên điện thoại; kết quả lá chưa có ngày và
nguồn; trang AI quá dài khi hiển thị cả 30 tác vụ và lặp cấu hình thời tiết.
Đã bổ sung timestamp cạnh nhóm cảm biến, ngày/nguồn kết quả lá, nút mở thêm lịch
sử AI và đưa cấu hình thời tiết về trang Lịch tưới.

Vòng xem ảnh cuối: thời điểm đo đọc được trên mobile, nguồn/ngày phân tích lá
hiển thị đầy đủ; trang AI mobile giảm từ khoảng 5385 px xuống 2421 px ở bộ dữ
liệu kiểm thử, vẫn truy cập được toàn bộ lịch sử. Không thấy chồng chữ hay tràn
ngang ở các viewport đã kiểm tra. Trạng thái mất backend có thông báo và số liệu
cũ, trạng thái relay hiển thị chưa xác định.

## Kết quả kiểm chứng — 16/10/2026

- `npm ci`: thành công sau khi dừng demo để giải phóng thư viện SWC trên Windows.
- `npm run lint`: thành công.
- `npm run build`: thành công, đủ sáu route nghiệp vụ. Cache type của đường dẫn
  cũ trong `.next/dev` được chuyển vào `.runtime/next-dev-before-redesign`, không
  xóa dữ liệu ứng dụng.
- `npm run test:e2e`: **4 passed**, 29,3 giây ở lượt cuối. Bao gồm luồng thật qua
  simulator/backend và upload MobileNetV3/YOLO; sitemap, Back/Reload, drawer
  Escape/focus, Tổng quan chỉ đọc; tính mốc lịch; mất mạng và phục hồi kết nối.
- Screenshot Playwright ở 1440, 768, 390 px cho Tổng quan; desktop/mobile cho cả
  năm trang chuyên sâu. Đã mở ảnh và tinh chỉnh sau lần chụp đầu tiên.
- `docker compose --env-file .env.example --profile local-mqtt config --quiet`:
  thành công. Script khởi động xác nhận PostgreSQL/MinIO/MQTT healthy và init
  thành công; demo cuối cùng chạy tại `http://localhost:3000`.
- `npm audit --omit=dev`: 0 vulnerabilities. `npm ci` vẫn báo 5 cảnh báo high
  trong cây dependency phát triển hiện có; không tự nâng major dependency.

Không sửa backend, firmware hay weights. Không chạy lại bộ unit/E2E backend
riêng trong phiên này; kiểm thử trình duyệt sử dụng backend và worker thật.
Không kiểm chứng phần cứng hoặc chất lượng mô hình từ ảnh ESP32-CAM. Ảnh fixture
và job phát sinh từ kiểm thử còn trong lịch sử như cơ chế demo hiện có.

Ảnh trong `.runtime/` (Git ignore), không chứa khóa truy cập:
`overview-1440.png`, `overview-768.png`, `overview-390.png`,
`overview-offline.png`, `{schedule,control,ai,history,simulation}-{1440,390}.png`.

## Tinh chỉnh giao diện chính

Lượt tinh chỉnh tiếp theo bổ sung vòng độ ẩm đất từ snapshot hiện có, làm rõ mục
điều hướng đang chọn bằng nền xanh đậm, đổi nền khối cây sang xanh olive nhạt,
tăng bán kính surface và làm nổi bật liên kết quản lý lịch. Khối cây chia thành
vùng trạng thái/độ ẩm và vùng kết quả lá; metadata kết quả mở bằng disclosure,
nhãn Mock cũ vẫn hiện trực tiếp nếu có.

Thẻ cảm biến có sparkline từ `history` backend; mỗi đường dùng thang tương đối
riêng, không biểu diễn ngưỡng hay mức an toàn. Không nối qua giá trị thiếu; dưới
hai giá trị hợp lệ thì báo chưa đủ lịch sử. Không thêm số liệu hoặc xu hướng giả.
Vòng độ ẩm hiển thị trạng thái dữ liệu cũ khi mất kết nối.

Đã chạy lại `npm ci`, lint, build và cả 4 kiểm thử Playwright. Đã xem ảnh Tổng
quan mới tại 1440/768/390 px: vòng độ ẩm, thẻ cảm biến và nhãn điều hướng hiển thị
đầy đủ, không tràn ngang. Backend, API, lịch và logic bơm giữ nguyên.

## Đồng bộ tương tác và phân trang lịch sử

- `workspace.css` bổ sung lớp trình bày dùng chung: trạng thái kết nối có nền
  xanh/cam, Đăng xuất có nền riêng, liên kết thao tác có nền và phản hồi hover.
- Tổng quan căn giữa lịch tiếp theo và trạng thái không có cảnh báo; các chỉ số
  kết nối có nền để phân biệt với nhãn. Form và dữ liệu dài vẫn căn trái.
- Lịch tưới phân nhóm tiêu đề, checkbox bật tự động, input và nút lưu chính.
  Trạng thái thời tiết và lựa chọn DEMO/LIVE có nền phân biệt.
- Mô phỏng dùng lưới thẻ 2 cột, có mô tả, trạng thái đang áp dụng và tên nút cho
  trình đọc màn hình. Callback và API đổi kịch bản giữ nguyên.
- Lịch sử tưới phân trang **6 lệnh/trang** trên tập tối đa 30 lệnh gần nhất do
  snapshot backend cung cấp, không phải phân trang toàn bộ database. Có Trước,
  Sau, số trang, số bản ghi, ngày theo giờ Việt Nam; sự kiện mở bằng “Xem chi tiết”.
  Trang được giới hạn theo số bản ghi hiện tại khi snapshot cập nhật.
- Test Playwright bổ sung kiểm tra số hàng, chuyển trang, nút biên bị vô hiệu hóa
  và mở chi tiết lệnh. Không tạo dữ liệu giả để kiểm thử phân trang.

Kiểm chứng lượt này: `npm ci`, `npm run lint`, `npm run build` thành công;
`npm run test:e2e` **5 passed (29,6 giây)** ở bản cuối. Đã xem ảnh bốn trang trên
desktop/mobile; sau vòng đầu sửa nhãn bảng bị xuống dòng và căn đều nút kịch bản.
Bảng mobile cuộn ngang trong vùng riêng, có hướng dẫn và hỗ trợ focus bàn phím.
Các ảnh trong `.runtime/` được cập nhật theo bản cuối.

## Xem lại quy tắc, dòng thời gian AI và ổn định cập nhật

- `saved-policy.tsx` hiển thị bản cấu hình backend đã lưu ở đầu trang Lịch tưới:
  trạng thái tự động, giờ/chu kỳ, ngưỡng đất, thời lượng, cooldown, nguồn thời tiết
  và mốc dự kiến tiếp theo. Nội dung này độc lập với bản nháp trong form; chỉ đổi
  khi backend trả dữ liệu đã lưu. Không suy ra cấu hình đã áp dụng từ thông báo.
- `ai-history.tsx` sắp xếp tác vụ theo `created_at` giảm dần, nhóm ngày theo
  `Asia/Ho_Chi_Minh`, có bộ lọc loại AI và phân trang 6 tác vụ. Mỗi tác vụ có thời
  điểm tạo, trạng thái, nguồn và thông tin model/kết quả. Tác vụ mưa hiển thị riêng
  khoảng dự báo, không trộn khoảng dự báo với thời điểm tạo job. Lịch sử vẫn giới
  hạn trong 30 tác vụ snapshot backend trả về.
- Thông báo thao tác dùng vị trí cố định để không đẩy form hoặc nội dung trang.
  Lỗi mất backend vẫn hiển thị trong luồng nội dung vì ảnh hưởng toàn bộ dữ liệu.
- Hai nội dung DEMO/LIVE cùng giữ không gian theo bố cục grid; phần không hoạt
  động dùng `inert`, `aria-hidden` và `visibility: hidden` để không nhận focus hay
  click. Metadata và lý do dự báo có chiều cao tối thiểu theo viewport. Tránh
  thay chiều cao toàn bộ panel khi đổi mode hay xuất hiện thông tin dự báo.
- Trong khi đổi nguồn thời tiết/kịch bản, các nút không đồng loạt giảm opacity.
  Khung ảnh AI có chiều cao ổn định trong lúc tải hoặc đổi kết quả.
- Client giữ snapshot mới hơn theo `server_time`; REST đến trễ không ghi đè
  WebSocket mới. Một REST cũ thất bại cũng không đổi giao diện sang mất kết nối
  nếu client đã nhận snapshot mới trong thời gian chờ yêu cầu đó.
- Form quy tắc chỉ remount khi cấu hình policy thực sự đổi; cập nhật telemetry
  hoặc nguồn thời tiết không xóa bản nháp của người dùng. AppShell và phiên dữ liệu
  tiếp tục tồn tại khi chuyển các route trong nhóm dashboard.

Kiểm thử bổ sung ở `frontend/tests/workflows.spec.ts`: xem bản lưu, lưu mà không
dịch chuyển form, chuyển DEMO/LIVE nhiều lần ở desktop/mobile, bảo toàn bản nháp,
kiểm tra `inert`, thứ tự/ngày/bộ lọc tác vụ AI và phát lại một phản hồi REST thật
đến trễ sau khi WebSocket đã nhận chế độ mới. Các lần đổi mode trong test được
khôi phục bằng `finally`; không thay logic scheduler, bơm hoặc model.

Kiểm chứng bản này: lint và production build thành công; toàn bộ Playwright
**8 passed (56,6 giây)**. Kiểm thử đo chênh lệch vị trí và chiều cao panel dưới
3 px qua nhiều lần chuyển DEMO/LIVE ở 1440/390 px; xác nhận bản nháp không đổi,
và phản hồi REST cũ không đảo ngược mode WebSocket mới. Đã xem ảnh desktop/mobile
của `saved-policy-{1440,390}.png` và `ai-timeline-{1440,390}.png` trong `.runtime/`.
Ảnh toàn trang chụp từ đầu trang để vị trí fixed của Sidebar và skip link không
bị lệch do trang đang cuộn trong lúc kiểm thử.

## Tự đóng thông báo thao tác

`action-feedback.tsx` quản lý thông báo thành công (4 giây) và lỗi thao tác
(8 giây). Rê chuột hoặc focus vào thông báo tạm dừng tự đóng; khi rời đi bắt đầu
lại thời gian đọc. Nút đóng thủ công vẫn dùng được. Mỗi thao tác mới có vòng đời
thông báo riêng; callback đóng ổn định để snapshot WebSocket không khởi động lại
bộ đếm. Timer được dọn khi thông báo bị đóng hoặc component unmount.

Cảnh báo backend mất kết nối và lỗi đăng nhập là trạng thái cần người dùng xử lý,
không dùng timer của thông báo thao tác. Playwright bổ sung kiểm tra tự đóng
thành công/lỗi khi realtime tiếp tục cập nhật và đóng bằng nút thủ công.

Kiểm chứng: `npm ci`, lint, build thành công; Playwright **9 passed (58,9 giây)**,
gồm kiểm thử mới về tự đóng thông báo trong khi WebSocket vẫn cập nhật.
