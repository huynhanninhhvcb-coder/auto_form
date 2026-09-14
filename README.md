# Auto Form

Ứng dụng Flask hỗ trợ người dân điền biểu mẫu hành chính từ PDF/ảnh giấy tờ hoặc qua Trợ lý phỏng vấn. Hiện hỗ trợ 3 biểu mẫu: **Văn bản đề nghị hưởng trợ cấp hưu trí xã hội**, **Tờ khai nhận chi phí hỗ trợ khuyến khích hỏa táng**, và **Tờ khai đề nghị hỗ trợ chi phí mai táng**.

Luồng xử lý:

```text
Ảnh/PDF → PDF text layer hoặc OCR → trích xuất & chuẩn hóa ┐
                                                          ├→ người dân rà soát → DOCX
Trợ lý phỏng vấn → hỏi/kiểm tra từng trường → tự điền ────┘
```

## Chạy ứng dụng trên Windows

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Mở `http://127.0.0.1:5000`.

PDF có sẵn lớp văn bản chạy ngay. Để đọc ảnh hoặc PDF quét, cài **Tesseract OCR** và gói ngôn ngữ tiếng Việt (`vie.traineddata`). Ứng dụng tự nhận bản cài chuẩn tại `C:\Program Files\Tesseract-OCR\tesseract.exe`. Với bản cài portable hoặc vị trí khác, đặt đường dẫn như sau:

```powershell
$env:TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
python app.py
```

## Cấu hình OCR trên máy chủ cấu hình thấp

Docker image và `render.yaml` đều bật sẵn chế độ OCR nhẹ cho Render Free: một
tác vụ Tesseract tại một thời điểm, ảnh tối đa 1200 px, chỉ nạp mô hình tiếng
Việt và bỏ lượt OCR không phù hợp với mặt thẻ CCCD đang đọc. Cấu hình trong
Docker giúp cả dịch vụ Render đã tạo thủ công (không đồng bộ Blueprint) vẫn
nhận được các giá trị này. Có thể kiểm tra cấu hình thực tế tại `/health`, trong
thuộc tính `ocr`.

Máy chủ nhiều CPU có thể bỏ `OCR_FAST_MODE`, tăng `OCR_TARGET_WIDTH` lên 1600,
dùng `OCR_LANGUAGE=vie+eng` và tăng `OCR_MAX_WORKERS` để ưu tiên độ chính xác/
thông lượng. Không nên tăng worker trên Render Free vì các tiến trình Tesseract
sẽ tranh cùng 0.1 CPU và có thể làm yêu cầu OCR lỗi 502.

## Quét nhiều tệp cùng lúc

Ở tab **Tải ảnh hoặc PDF**, có thể chọn hoặc kéo-thả tối đa **5** ảnh/PDF trong một lượt (tối đa **12 MB mỗi tệp**, **60 MB tổng cộng**). Có thể bổ sung tệp qua nhiều lần chọn, bỏ từng tệp hoặc xóa toàn bộ danh sách trước khi quét.

Mỗi tệp được OCR/trích xuất riêng rồi phần mềm gộp thông tin có độ tin cậy cao nhất vào biểu mẫu. Nếu hai tệp có thông tin mâu thuẫn, trường đó được tô vàng và có cảnh báo để người dân kiểm tra; một tệp lỗi sẽ không làm mất dữ liệu đã đọc được từ các tệp còn lại.

## Quét CCCD

Để đọc CCCD chính xác hơn, hãy tải **đồng thời mặt trước và mặt sau** trong cùng một lượt quét. Phần mềm nhận diện nhãn song ngữ trên mặt trước, đọc vùng MRZ ở mặt sau và chỉ sử dụng dữ liệu MRZ khi các mã kiểm tra hợp lệ. Tên có dấu từ mặt trước được ưu tiên khi ghép với tên không dấu trong MRZ.

Địa chỉ trên CCCD vẫn luôn được tô vàng để người dùng rà soát trước khi tạo đơn, vì ảnh chụp nghiêng, lóa hoặc mờ có thể làm sai một vài ký tự. Ảnh nên đủ sáng, không lóa, thấy trọn thẻ và có độ phân giải rõ.

## AI hỗ trợ trích xuất (tùy chọn)

Khi OCR/quy tắc trích xuất không đọc được một trường (ví dụ ảnh mờ, chữ viết tay, bố cục lạ), phần mềm có thể gọi GPT vision (OpenAI) để đọc lại **ảnh gốc** của tệp đó và bổ sung riêng những trường còn thiếu. Tesseract/regex vẫn luôn chạy trước và là nguồn chính; AI chỉ được gọi cho từng trường trống, không thay thế toàn bộ pipeline, và giá trị AI đọc được luôn bị đánh dấu "cần rà soát" như các trường có độ tin cậy trung bình khác.

Trên giao diện, người dân phải chủ động đánh dấu **Cho phép AI hỗ trợ nhận diện** trước khi tài liệu được gửi tới OpenAI. Nếu không đồng ý hoặc API hết hạn mức, ứng dụng chỉ dùng Tesseract và vẫn tiếp tục xử lý bình thường. Theo [tài liệu kiểm soát dữ liệu OpenAI](https://developers.openai.com/api/docs/guides/your-data), dữ liệu API mặc định không được dùng để huấn luyện mô hình nếu tổ chức không chủ động tham gia chia sẻ; nhật ký giám sát lạm dụng mặc định có thể được giữ tối đa 30 ngày.

1. Sao chép `.env.example` thành `.env`.
2. Điền `OPENAI_API_KEY` (tạo tại https://platform.openai.com/api-keys). Để trống để tắt hẳn tính năng này — ứng dụng vẫn chạy OCR/regex như cũ, không gọi mạng ngoài, không phát sinh chi phí.
3. Tùy chọn đổi `OPENAI_MODEL` (mặc định `gpt-4o-mini`; đổi sang `gpt-4o` nếu cần đọc chính xác hơn với ảnh khó).

**Không commit `.env` lên git và không dán API key ra ngoài** (chat, issue, log...). Nếu key từng bị lộ, thu hồi ngay trên trang OpenAI rồi tạo key mới.

## Bảo vệ dữ liệu

- Tệp nguồn được xóa ngay sau khi OCR/trích xuất xong.
- Tệp DOCX được xóa ngay sau khi nạp vào phản hồi tải xuống.
- Ứng dụng không ghi số CCCD hay nội dung OCR vào cơ sở dữ liệu.
- Dữ liệu biểu mẫu trong Trợ lý phỏng vấn chỉ ở RAM tối đa 30 phút. Khi dùng giọng nói, Web Speech API của trình duyệt có thể dùng dịch vụ nhận dạng giọng nói của trình duyệt; máy chủ không nhận hoặc lưu tệp âm thanh.
- Kết quả OCR chỉ là gợi ý; người dùng phải rà soát và xác nhận trước khi sử dụng.

## Thêm biểu mẫu mới

1. Đặt mẫu `.docx` vào `templates_word/`. Với mẫu mới, ưu tiên các placeholder như `{{full_name}}`, `{{citizen_id}}`, `{{date_of_birth}}` — cách này không phụ thuộc nhãn/định dạng gốc và là cách hai mẫu hỏa táng, mai táng bên dưới dùng.
2. Thêm một `FormTemplate` trong `services/template_service.py` (title, tên file, `required_fields`).
3. Nếu mẫu dùng các nhãn có sẵn thay vì placeholder (như mẫu trợ cấp hưu trí, mẫu cũ hơn), thêm mapping riêng trong `services/docx_service.py`.
4. Nếu mẫu cần trường dữ liệu chưa có, thêm vào `FIELD_NAMES` trong `services/extraction_service.py`, rồi thêm ô nhập tương ứng trong `templates/index.html` — bọc trong khối `class="template-fields" data-template="<id-biểu-mẫu>"` (nhiều id cách nhau bằng dấu cách nếu trường dùng chung cho nhiều biểu mẫu) để ô chỉ hiện khi biểu mẫu đó được chọn (xem `setTemplateFields` trong `static/js/app.js`).

Các trường hiện dùng: `full_name`, `date_of_birth`, `gender`, `ethnic_group`, `citizen_id`, `residence_address`, `contact_address`, `phone_number`, nhóm tài khoản ngân hàng, nhóm người giám hộ (`guardian_*`), và nhóm người đã mất/tổ chức lo mai táng (`deceased_*`, `death_certificate_*`, `org_*`) dùng cho hai mẫu hỏa táng/mai táng.

## Trợ lý phỏng vấn

Chọn tab **Trợ lý phỏng vấn** ở bước 1 khi không có PDF/ảnh. Micro nhận câu trả lời với ngôn ngữ `vi-VN`; mọi bản chép lời đều phải được người dân xác nhận hoặc chọn nói lại trước khi câu trả lời được gửi vào biểu mẫu. CCCD/điện thoại có thể đọc từng chữ số, ví dụ “không, bảy, chín…”.

Bộ câu hỏi thay đổi theo biểu mẫu đang chọn ở Bước 1 (`services/interview_service.py`, `TEMPLATE_STEPS`): mẫu trợ cấp hưu trí hỏi về bản thân người đề nghị; hai mẫu hỏa táng/mai táng hỏi thêm về người đã mất (họ tên, ngày mất là bắt buộc; ngày sinh, giới tính, dân tộc, nơi cư trú, CCCD, nơi/nguyên nhân mất, giấy chứng tử là tùy chọn) và tổ chức đứng ra lo hậu sự nếu có. Thêm biểu mẫu mới thì cũng cần thêm một bộ câu hỏi tương ứng vào `TEMPLATE_STEPS`, nếu không trợ lý sẽ dùng lại bộ câu hỏi của mẫu trợ cấp hưu trí.

Để trợ lý **đọc** câu hỏi bằng tiếng Việt, trình duyệt phải có một giọng đọc TTS tiếng Việt. Giao diện chỉ chọn giọng có ngôn ngữ `vi`/`vi-VN`; nếu không có, câu hỏi vẫn hiện bằng chữ và ứng dụng nêu hướng dẫn cài đặt thay vì đọc bằng giọng tiếng Anh. Trên Windows, vào **Cài đặt > Giọng nói hoặc Trình tường thuật > Thêm giọng nói**, thêm **Tiếng Việt**, rồi khởi động lại Chrome/Edge. Microsoft liệt kê giọng tiếng Việt là **An** trong [danh sách ngôn ngữ và giọng TTS được hỗ trợ](https://support.microsoft.com/vi-vn/accessibility/windows/narrator/appendix-a-supported-languages-and-voices).

Chrome hoặc Edge hỗ trợ Web Speech API tốt nhất. Khi triển khai công khai cần dùng HTTPS để trình duyệt cấp quyền micro. Giao diện luôn có phương án nhập bàn phím khi micro không khả dụng. Khi hoàn tất, mọi câu trả lời được chèn vào phần rà soát; người dân vẫn cần xác nhận trước khi xuất đơn.

## Chatbot hướng dẫn thủ tục hành chính

Nút tròn ở góc phải dưới màn hình mở một trợ lý riêng, trả lời câu hỏi tự do về các thủ tục hành chính (hồ sơ cần gì, nộp ở đâu, mất bao lâu, có mất phí không...) bằng cách so khớp câu hỏi với dữ liệu `.txt` cục bộ trong `thutuc_data/`. Hỗ trợ gõ hoặc hỏi bằng giọng nói (`vi-VN`), có 4 nút truy cập nhanh: Tra cứu BHYT, Mẫu đơn/tờ khai (đọc từ một thư mục Google Drive công khai), Khu phố, và Danh sách thủ tục.

Thêm/sửa thủ tục bằng cách thêm một file `.txt` vào `thutuc_data/`, đánh số các mục (`1. Tên thủ tục`, `2. Đối tượng áp dụng:`, `3. Thành phần hồ sơ:`...); xem các file mẫu có sẵn trong thư mục để theo đúng định dạng.

Cấu hình trong `.env` (xem `.env.example`):

- `AUTO_FORM_SECRET_KEY`: bắt buộc để chatbot nhớ được thủ tục đang hỏi dở giữa các câu hỏi liên tiếp (dùng Flask session). Tạo bằng `python -c "import secrets; print(secrets.token_hex(32))"`.
- `GOOGLE_DRIVE_API_KEY`, `MAU_DON_FOLDER_ID`: để hiển thị danh sách thư mục mẫu đơn từ Google Drive. Để trống nếu chưa cần tính năng này — chatbot vẫn trả lời các câu hỏi khác bình thường, chỉ riêng nút "Mẫu đơn" báo chưa cấu hình.

### Trả lời bằng ChatGPT khi chưa có dữ liệu cục bộ

Khi câu hỏi không khớp thủ tục nào trong `thutuc_data/`, chatbot có thể hiện nút **"🤖 Hỏi trợ lý AI"** thay vì tự động gửi câu hỏi ra ngoài. Chỉ khi người dân bấm nút đó, câu hỏi mới được gửi cho ChatGPT (dùng chung `OPENAI_API_KEY`/`OPENAI_MODEL` đã cấu hình cho tính năng AI hỗ trợ trích xuất ở trên). Câu trả lời AI luôn kèm cảnh báo đây là thông tin tham khảo, chưa được xác minh chính thức, và khuyên liên hệ trực tiếp Trung tâm phục vụ hành chính công.

Tính năng này có công tắc riêng, **mặc định TẮT**: `CHATBOT_AI_ENABLED=true` trong `.env` để bật. Khi tắt (mặc định), nút không hiện và chatbot chỉ trả lời từ dữ liệu cục bộ — không liên quan đến `OPENAI_API_KEY` (vẫn cần đặt key hợp lệ để dùng, nhưng có key không tự động bật tính năng). Tách riêng khỏi tính năng AI hỗ trợ trích xuất OCR ở trên, để tắt/bật độc lập nhau.

## Kiểm thử

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
