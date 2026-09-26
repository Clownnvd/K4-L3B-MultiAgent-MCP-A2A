# KINGPRO — cấu hình competition Day 09

- Team: **KINGPRO**.
- Variant bài thi: **L3B**.
- Lớp trên ảnh xác nhận đăng ký: **H209**.
- Đại diện: **Nguyễn Văn Duy**, MSSV `2A202602729`, hậu tố đăng ký `02729`.
- Thành viên: **Dương Thị Ngân**, MSSV `2A2026022808`, tài khoản GitHub `nganduong-123`.
- Competition Workspace: https://n7-competition.pages.dev
- Repo đề gốc: https://github.com/VinUni-AI20k/K4-L3B-MultiAgent-MCP-A2A
- Fork làm việc: https://github.com/Clownnvd/K4-L3B-MultiAgent-MCP-A2A

## Vai trò nhóm

| Thành viên | Vai trò tổng quát | Phạm vi | Bằng chứng/trạng thái |
|---|---|---|---|
| Nguyễn Văn Duy | Trưởng nhóm; Orchestration & Integration | Orchestrator, model adapter/deployment, live MCP, batch/checkpoint, verifier, packaging và quyết định submission | Các nhánh `work/v0.1.0-scaffold` đến `work/v0.6-grounding-efficiency`; commit mới nhất `28bb92c` |
| Dương Thị Ngân | Evidence & Rules | Audit source facts, decision plan, grounded output, evidence/provenance rules và test tương ứng | Commit `9cb354c`; nhánh `ngan/evidence-rules`; pull request đang mở vào `work/v0.2-model-ready` |

Phân công trên chỉ mô tả ownership (phạm vi chịu trách nhiệm). Không ghi nhận một thành viên là tác giả của code do người khác viết nếu chưa có commit/PR của chính thành viên đó.

## Giới hạn model

Không dùng model có **tổng số tham số lớn hơn 10 tỷ** trong bài thi. Lượng tử hóa không làm giảm số tham số. Model hiện tại là `Qwen/Qwen3.5-9B`, đã xác minh tổng số tham số là `9.653.104.368`.

`MODEL_MAX_TOTAL_PARAMETERS` trong `.env` ghi lại ràng buộc này. `model_policy.py` và allowlist (danh sách cho phép) trong `model_adapter.py` chỉ nhận Qwen3-8B và Qwen3.5-9B với số tham số đã xác minh. Model đã chạy JSON smoke test trên H200; kết quả model không được tin trực tiếp mà phải qua grounded output builder và verifier.

## Khóa truy cập

Team API Key chỉ lưu trong `.env` cục bộ; `.gitignore` đã chặn file này. Không đưa khóa vào source, log, báo cáo, ZIP bài nộp hoặc Git. Credential ID trên màn hình đăng ký không thay thế Team API Key.

## Trạng thái

Đây là bài competition L3B mới, tách biệt với bài cũ trong `K4-Day9-Multi-Agent-A2A`. Phiên bản nhóm đang sử dụng ở nhánh `work/v0.6-grounding-efficiency`, commit `28bb92c`. Toàn luồng giả lập 100 case, model boundary, live MCP, batch/checkpoint, resume, packaging guard và verifier đã có test. Trạng thái chạy thật và submission phải lấy từ receipt/artifact tương ứng; không suy ra chỉ từ tên ZIP hoặc source commit. Chi tiết vận hành ở `docs/IMPLEMENTATION_STATUS.md`, `docs/RUNBOOK.md` và `docs/VERSIONING.md`.

## Quy tắc làm việc nhóm

- Mỗi người làm trên branch riêng và mở PR; không push thẳng vào nhánh phiên bản đã chốt.
- Commit phải dùng đúng tài khoản cá nhân và chỉ nhận phần việc có thể giải thích, chạy test và chỉ ra file thay đổi.
- Mọi PR phải qua `pytest`, `ruff` và secret scan trước khi merge.
- Không commit `.env`, credential, official inputs, generated outputs, traces, local evidence dumps hoặc competition ZIP.
- `ARCHITECTURE.md` phải được cập nhật cùng thay đổi kiến trúc; README chỉ giữ hướng dẫn chung của đề.
