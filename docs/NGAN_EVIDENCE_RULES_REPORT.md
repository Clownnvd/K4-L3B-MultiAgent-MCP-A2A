# Báo cáo đóng góp cá nhân — Dương Thị Ngân

## Thông tin

- Họ và tên: Dương Thị Ngân
- Mã học viên: 2A2026022808
- Nhóm: K4-L3B
- Repository: `Clownnvd/K4-L3B-MultiAgent-MCP-A2A`
- Nhánh: `ngan/evidence-rules`
- Nhánh đích: `work/v0.6-grounding-efficiency`
- Base hiện tại: `98e194f1988da90da15a797db9f977c2cf13d91c`

## Phần việc trực tiếp thực hiện

1. Đọc và kiểm tra gói handoff Evidence & Rules gồm 16 file từ snapshot `dd96588`.
2. Đối chiếu SHA-256 của toàn bộ file overlay với `MANIFEST.json`; kết quả 16/16 file khớp.
3. Xác nhận snapshot Evidence & Rules là ancestor của nhánh mới nhất `work/v0.6-grounding-efficiency`, sau đó chuyển đóng góp cá nhân lên v0.6 mà không ghi đè các cải tiến mới.
4. Kiểm tra các invariant về evidence scope, entity resolution, grounded output, phép tính tiền và đóng gói batch.
5. Bổ sung test cá nhân `test_reused_evidence_ref_cannot_change_contents` trong `tests/test_safety.py`.
6. Chạy toàn bộ unit test, Ruff và source-safety scan trước khi push.

## Invariant đã xác minh

Một `evidence_ref` phải bất biến trong phạm vi ledger của cùng một case. Nếu gateway trả lại cùng reference nhưng payload đã đổi, hệ thống phải từ chối thay vì ghi đè hoặc tiếp tục suy luận. Điều này ngăn một bằng chứng đã được trích dẫn bị đổi nội dung sau khi agent khác đã tiêu thụ nó.

Test cá nhân gọi hai lần `get_order` với hai `order_id` khác nhau nhưng gateway cố tình tái sử dụng cùng `evidence_ref`. Lần thứ hai phải phát sinh `ValueError` với thông báo `changed its contents`.

## Bằng chứng kiểm tra

```powershell
python -m pytest -q
python -m ruff check src tests tools
python tools/check_source_safety.py
```

Kết quả trên base v0.6: `265 passed in 27.78s`; Ruff pass; source-safety quét 90 file và trả về `Safety issues: []`.

## Phạm vi không nhận ownership

Không nhận là người tạo dữ liệu thi, credential, input/output thật, trace thật hoặc kết quả chấm. Không commit `.env`, API key, `inputs/`, `outputs/`, `traces/`, `.local/` hay ZIP thi.
