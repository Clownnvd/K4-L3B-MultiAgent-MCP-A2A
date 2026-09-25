# KINGPRO — cấu hình competition Day 09

- Team: **KINGPRO**.
- Variant bài thi: **L3B**.
- Lớp trên ảnh xác nhận đăng ký: **H209**.
- Đại diện: **Nguyễn Văn Duy**, MSSV `2A202602729`, hậu tố đăng ký `02729`.
- Thành viên bổ sung: **Dương Thị Ngân**; MSSV do Duy cung cấp: `2A2026022808`.
- Competition Workspace: https://n7-competition.pages.dev
- Repo đề: https://github.com/VinUni-AI20k/K4-L3B-MultiAgent-MCP-A2A

## Giới hạn model

Không dùng model có **tổng số tham số lớn hơn 10 tỷ** trong bài thi. Lượng tử hóa không làm giảm số tham số. Qwen3.5-9B là ứng viên đã chọn để thử; chưa kết nối dịch vụ chạy mô hình.

`MODEL_MAX_TOTAL_PARAMETERS` trong `.env` ghi lại ràng buộc này. Mã kiểm tra ở `model_policy.py` và danh sách cho phép ở `model_adapter.py` nhận Qwen3-8B và Qwen3.5-9B với số tham số đã xác minh. Mô hình thật vẫn cần kiểm tra kết nối trước khi xử lý bài thi.

## Khóa truy cập

Team API Key chỉ lưu trong `.env` cục bộ; `.gitignore` đã chặn file này. Không đưa khóa vào source, log, báo cáo, ZIP bài nộp hoặc Git. Credential ID trên màn hình đăng ký không thay thế Team API Key.

## Trạng thái

Đây là bản đang phát triển, tách biệt với bài cũ trong `K4-Day9-Multi-Agent-A2A`. Đã kiểm tra toàn luồng trên 100 tình huống giả lập. Workspace đã mở bằng key hợp lệ; chưa có kết quả từ mô hình thật hay bài nộp. Trạng thái chi tiết ở `docs/IMPLEMENTATION_STATUS.md`.
