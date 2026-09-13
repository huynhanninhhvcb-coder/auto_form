"""Điểm vào của ứng dụng điền biểu mẫu từ ảnh/PDF."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, render_template, request, send_file, session
from werkzeug.exceptions import RequestEntityTooLarge

from config import Config
from services.ai_extraction_service import AIExtractionError, extract_missing_fields, is_configured
from services.batch_extraction_service import merge_extraction_results
import services.chatbot_ai_service as chatbot_ai_service
import services.chatbot_service as chatbot_service
from services.docx_service import DocumentGenerationError, generate_document
from services.extraction_service import extract_personal_information
from services.interview_service import InterviewNotFoundError, InterviewService
from services.ocr_service import OCRUnavailableError, ocr_image
from services.pdf_service import PDFProcessingError, process_pdf
from services.template_service import get_template, list_templates
from services.validation_service import validate_form_data


class UploadTooLargeError(ValueError):
    """A single upload exceeded the per-file batch limit."""


def _allowed_file(filename: str, allowed_extensions: set[str]) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_extensions


def _display_filename(filename: str) -> str:
    """Return a display-only basename without ever using it as a filesystem path."""
    return filename.replace("\\", "/").rsplit("/", 1)[-1] or "tệp không tên"


def _selected_uploads() -> list:
    """Read the new `files` field and retain compatibility with legacy `file`."""
    uploads = [upload for upload in request.files.getlist("files") if upload and upload.filename]
    if not uploads:
        uploads = [upload for upload in request.files.getlist("file") if upload and upload.filename]
    return uploads


def _save_upload_with_limit(upload, destination: Path, maximum_size: int) -> int:
    """Stream an upload to a temporary file while enforcing its individual limit."""
    total_size = 0
    with destination.open("wb") as output:
        while chunk := upload.stream.read(1024 * 1024):
            total_size += len(chunk)
            if total_size > maximum_size:
                raise UploadTooLargeError
            output.write(chunk)
    return total_size


def _unique_warnings(warnings: list[str]) -> list[str]:
    return list(dict.fromkeys(warning for warning in warnings if warning))


def _ai_requested() -> bool:
    """Require explicit consent before sending a citizen document externally."""
    return request.form.get("use_ai", "").strip().lower() in {"1", "true", "yes", "on"}


# Chỉ những trường OCR/regex thực sự cố nhận diện từ giấy tờ mới đáng gọi AI bổ
# sung; các trường như thông tin người giám hộ được thu thập qua phỏng vấn,
# không nằm trên giấy tờ nên hỏi AI cũng sẽ luôn trống.
_AI_EXTRACTABLE_FIELDS = (
    "full_name",
    "date_of_birth",
    "gender",
    "ethnic_group",
    "citizen_id",
    "residence_address",
    "contact_address",
    "phone_number",
    "bank_account_name",
    "bank_account_number",
    "bank_name",
)


def _enhance_with_ai(
    app: Flask,
    filepath: Path,
    extension: str,
    processed,
    extracted,
    *,
    consented: bool,
) -> None:
    """Gọi GPT vision để bổ sung trường còn thiếu, nếu đã cấu hình OPENAI_API_KEY.

    Phải chạy trước khi tệp tạm bị xóa vì cần gửi ảnh gốc (không phải văn bản
    OCR) cho AI đọc lại.
    """
    if not consented or not is_configured(app.config):
        return

    folded_text = processed.text.casefold()
    if any(marker in folded_text for marker in ("tài khoản", "tai khoan", "ngân hàng", "ngan hang")):
        relevant_fields = ("bank_account_name", "bank_account_number", "bank_name")
    elif any(marker in folded_text for marker in ("căn cước", "can cuoc", "citizen identity", "idvnm", "1dvnm")):
        relevant_fields = (
            "full_name",
            "date_of_birth",
            "gender",
            "citizen_id",
            "residence_address",
            "contact_address",
        )
    else:
        relevant_fields = _AI_EXTRACTABLE_FIELDS

    review_fields = [
        field
        for field in relevant_fields
        if not extracted.fields.get(field)
        or extracted.confidence.get(field, "missing") in {"missing", "low", "medium"}
    ]
    if not review_fields:
        return
    try:
        ai_fields = extract_missing_fields(
            filepath,
            extension,
            review_fields,
            api_key=app.config["OPENAI_API_KEY"],
            model=app.config["OPENAI_MODEL"],
            max_pages=app.config["MAX_PDF_PAGES"],
        )
    except AIExtractionError as error:
        processed.warnings.append(f"Không thể dùng AI để bổ sung dữ liệu còn thiếu: {error}")
        return
    if not ai_fields:
        return
    for field, value in ai_fields.items():
        extracted.fields[field] = value
        extracted.confidence[field] = "medium"
    processed.warnings.append(
        "Một số trường được ChatGPT hỗ trợ nhận diện do OCR không đọc được; hãy kiểm tra kỹ trước khi tạo đơn."
    )


def _combined_raw_text(records: list[dict], maximum_length: int = 12000) -> str:
    """Keep an inspectable, bounded OCR preview for a multi-document request."""
    chunks: list[str] = []
    remaining = maximum_length
    for record in records:
        header = f"--- Tệp: {record['name']} ---\n"
        text = record["processed"].text.strip()
        chunk = f"{header}{text}".strip()
        if not chunk or remaining <= 0:
            break
        if len(chunk) > remaining:
            chunks.append(chunk[:remaining])
            break
        chunks.append(chunk)
        remaining -= len(chunk) + 2
    return "\n\n".join(chunks)


def _process_saved_upload(app: Flask, name: str, filepath: Path, extension: str, ai_consented: bool) -> dict:
    """Chạy OCR + trích xuất cho một tệp đã lưu tạm; luôn dọn tệp khi xong.

    Tách khỏi vòng lặp lưu tệp để nhiều tệp trong cùng một lượt tải lên có
    thể được OCR song song (xem ``_extract_batch``): đây là phần chậm nhất
    của yêu cầu và độc lập giữa các tệp.
    """
    try:
        if extension == "pdf":
            processed = process_pdf(
                filepath,
                max_pages=app.config["MAX_PDF_PAGES"],
                tesseract_cmd=app.config.get("TESSERACT_CMD"),
                max_workers=app.config["OCR_MAX_WORKERS"],
            )
        else:
            processed = ocr_image(
                filepath,
                tesseract_cmd=app.config.get("TESSERACT_CMD"),
                max_workers=app.config["OCR_MAX_WORKERS"],
            )
        extracted = extract_personal_information(
            processed.text,
            page_texts=processed.pages,
            cccd_mrz_texts=processed.mrz_texts,
        )
        _enhance_with_ai(app, filepath, extension, processed, extracted, consented=ai_consented)
    except OCRUnavailableError as error:
        return {
            "name": name,
            "status": "error",
            "message": "Không thể OCR tệp quét trên máy chủ này.",
            "detail": str(error),
            "code": "ocr_unavailable",
        }
    except (PDFProcessingError, OSError, ValueError) as error:
        return {
            "name": name,
            "status": "error",
            "message": "Không thể đọc tệp đã tải lên.",
            "detail": str(error),
            "code": "unreadable_file",
        }
    finally:
        # Tệp nguồn chứa dữ liệu cá nhân chỉ tồn tại trong suốt yêu cầu này.
        filepath.unlink(missing_ok=True)
    return {"name": name, "status": "processed", "processed": processed, "extracted": extracted}


def _extract_batch(app: Flask, uploads: list):
    """Process each document independently, then merge only reviewable fields.

    A corrupt scan must not discard useful information read from the other
    documents.  Limits, however, fail the whole request because the user needs
    to reduce the selected batch before retrying.

    Saving (fast, must stay sequential to enforce the running batch-size
    total) and OCR (slow, each file spawns several independent Tesseract
    subprocesses) are split into two passes so the OCR pass can run the
    files concurrently: on Windows a single OCR call has been measured at
    0.4-1.3 seconds, so processing a 5-file batch one file at a time can add
    several extra seconds of pure waiting for no benefit.
    """
    processed_files: list[dict] = []
    failures: list[dict] = []
    uploaded_size = 0
    ai_consented = _ai_requested()
    maximum_file_size = app.config["MAX_FILE_SIZE"]
    maximum_batch_size = app.config["MAX_BATCH_TOTAL_SIZE"]
    file_size_mb = maximum_file_size // (1024 * 1024)
    batch_size_mb = maximum_batch_size // (1024 * 1024)

    # Giữ vị trí tải lên ban đầu cho processed_files: tệp lỗi ngay khi lưu (hiếm,
    # ví dụ hết dung lượng đĩa) không được chạy qua OCR song song bên dưới, nên
    # phải ghi nhớ chỉ số gốc để không xáo trộn thứ tự hiển thị so với các tệp
    # còn lại của cùng lượt tải lên.
    processed_files_by_index: dict[int, dict] = {}
    saved_files: list[tuple[int, str, Path, str]] = []
    for index, upload in enumerate(uploads):
        name = _display_filename(upload.filename)
        extension = upload.filename.rsplit(".", 1)[1].lower()
        declared_size = upload.content_length or 0
        if declared_size > maximum_file_size:
            return jsonify(error=f"Tệp \"{name}\" lớn hơn giới hạn {file_size_mb} MB mỗi tệp."), 413
        if declared_size and uploaded_size + declared_size > maximum_batch_size:
            return jsonify(error=f"Tổng dung lượng các tệp vượt quá {batch_size_mb} MB."), 413

        remaining_size = maximum_batch_size - uploaded_size
        if remaining_size <= 0:
            return jsonify(error=f"Tổng dung lượng các tệp vượt quá {batch_size_mb} MB."), 413

        filepath = Path(app.config["UPLOAD_FOLDER"]) / f"{uuid4().hex}.{extension}"
        save_limit = min(maximum_file_size, remaining_size)
        try:
            uploaded_size += _save_upload_with_limit(upload, filepath, save_limit)
        except UploadTooLargeError:
            filepath.unlink(missing_ok=True)
            error = (
                f"Tổng dung lượng các tệp vượt quá {batch_size_mb} MB."
                if save_limit < maximum_file_size
                else f"Tệp \"{name}\" lớn hơn giới hạn {file_size_mb} MB mỗi tệp."
            )
            return jsonify(error=error), 413
        except OSError as error:
            filepath.unlink(missing_ok=True)
            failure = {
                "name": name,
                "message": "Không thể đọc tệp đã tải lên.",
                "detail": str(error),
                "code": "unreadable_file",
            }
            failures.append(failure)
            processed_files_by_index[index] = {"name": name, "status": "error", **failure}
            continue
        saved_files.append((index, name, filepath, extension))

    if saved_files:
        batch_workers = min(len(saved_files), app.config["OCR_MAX_WORKERS"])
        with ThreadPoolExecutor(max_workers=batch_workers) as executor:
            outcomes = list(
                executor.map(
                    lambda item: _process_saved_upload(app, item[1], item[2], item[3], ai_consented),
                    saved_files,
                )
            )
    else:
        outcomes = []

    records: list[dict] = []
    for (index, _name, _filepath, _extension), outcome in zip(saved_files, outcomes):
        if outcome["status"] == "processed":
            records.append(outcome)
            processed_files_by_index[index] = {
                "name": outcome["name"],
                "status": "processed",
                "source": outcome["processed"].source,
                "page_count": len(outcome["processed"].pages),
                "extracted_fields": [
                    field for field, value in outcome["extracted"].fields.items() if value
                ],
            }
        else:
            failure = {key: outcome[key] for key in ("name", "message", "detail", "code")}
            failures.append(failure)
            processed_files_by_index[index] = {"name": outcome["name"], "status": "error", **failure}

    processed_files = [processed_files_by_index[index] for index in sorted(processed_files_by_index)]

    if not records:
        response_code = 503 if failures and all(item["code"] == "ocr_unavailable" for item in failures) else 422
        error = (
            "Không thể OCR các tệp quét trên máy chủ này."
            if response_code == 503
            else "Không thể đọc các tệp đã tải lên."
        )
        return jsonify(error=error, processed_files=processed_files), response_code

    merged = merge_extraction_results(record["extracted"] for record in records)
    validation = validate_form_data(merged.result.fields, required_fields=())
    warnings = list(merged.result.warnings)
    for record in records:
        warnings.extend(f"{record['name']}: {warning}" for warning in record["processed"].warnings)
    warnings.extend(f"{failure['name']}: {failure['message']}" for failure in failures)
    warnings.extend(validation.warnings)

    return jsonify(
        fields=merged.result.fields,
        field_confidence=merged.result.confidence,
        warnings=_unique_warnings(warnings),
        source="batch",
        processed_file_count=len(records),
        processed_files=processed_files,
        conflicts=[{"field": conflict.field, "label": conflict.label} for conflict in merged.conflicts],
        # Giữ văn bản đối chiếu trong phản hồi hiện tại; không ghi ra cơ sở dữ liệu.
        raw_text=_combined_raw_text(records),
    )


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    for folder_name in ("UPLOAD_FOLDER", "OUTPUT_FOLDER", "TEMPLATE_FOLDER", "THUTUC_FOLDER"):
        Path(app.config[folder_name]).mkdir(parents=True, exist_ok=True)
    app.extensions["interview_service"] = InterviewService(app.config["INTERVIEW_TTL_SECONDS"])

    @app.get("/")
    def index():
        danh_sach_thutuc = [ten for _f, ten in chatbot_service.list_thutuc_names(app.config["THUTUC_FOLDER"])]
        return render_template(
            "index.html",
            templates=list_templates(),
            ai_configured=is_configured(app.config),
            danh_sach_thutuc=danh_sach_thutuc,
        )

    @app.get("/health")
    def health():
        return jsonify(
            status="ok",
            tesseract_configured=bool(app.config.get("TESSERACT_CMD")),
            ai_configured=is_configured(app.config),
            ai_model=app.config.get("OPENAI_MODEL") if is_configured(app.config) else None,
        )

    @app.post("/api/chatbot/chat")
    def chatbot_chat():
        payload = request.get_json(silent=True) or {}
        message = str(payload.get("message", ""))
        ai_consented = bool(payload.get("use_ai"))
        answer = chatbot_service.answer_question(
            app.config["THUTUC_FOLDER"],
            message,
            session.get("chatbot_last_procedure"),
        )
        if answer.context_action == "clear":
            session.pop("chatbot_last_procedure", None)
        elif answer.context_action == "set":
            session["chatbot_last_procedure"] = answer.context_value

        reply = answer.reply
        suggestions = answer.suggestions
        # Câu hỏi không khớp thủ tục nào cục bộ: chỉ gửi cho ChatGPT khi tính
        # năng đang được bật (CHATBOT_AI_ENABLED) và người dùng đã bấm xác
        # nhận cho CHÍNH câu hỏi này (offer_ai chỉ đề nghị, không tự động gửi
        # dữ liệu ra ngoài ứng dụng).
        offer_ai = (
            answer.offer_ai
            and app.config.get("CHATBOT_AI_ENABLED", False)
            and chatbot_ai_service.is_configured(app.config)
        )
        if offer_ai and ai_consented:
            try:
                known_procedures = [
                    ten for _f, ten in chatbot_service.list_thutuc_names(app.config["THUTUC_FOLDER"])
                ]
                reply = chatbot_ai_service.answer_question(
                    message,
                    known_procedures,
                    api_key=app.config["OPENAI_API_KEY"],
                    model=app.config["OPENAI_MODEL"],
                )
                suggestions = None
            except chatbot_ai_service.ChatbotAIError as error:
                reply = f"{reply}\n\n⚠️ Không thể dùng AI hỗ trợ trả lời: {error}"
            offer_ai = False

        return jsonify(reply=reply, suggestions=suggestions, offer_ai=offer_ai)

    @app.post("/api/chatbot/clear-context")
    def chatbot_clear_context():
        # Xóa ngữ cảnh thủ tục đang hỏi dở khi người dùng bấm "Xóa hội thoại".
        session.pop("chatbot_last_procedure", None)
        return "", 204

    @app.get("/mau-don")
    def mau_don():
        folders, error = chatbot_service.list_mau_don_folders(
            app.config.get("GOOGLE_DRIVE_API_KEY"), app.config.get("MAU_DON_FOLDER_ID")
        )
        return render_template("mau_don.html", folders=folders, error=error)

    @app.post("/api/extract")
    def extract():
        uploads = _selected_uploads()
        if not uploads:
            return jsonify(error="Vui lòng chọn ít nhất một tệp PDF hoặc ảnh."), 400
        if len(uploads) > app.config["MAX_UPLOAD_FILES"]:
            return jsonify(
                error=f"Chỉ có thể tải tối đa {app.config['MAX_UPLOAD_FILES']} tệp trong một lần quét."
            ), 400

        unsupported = [
            _display_filename(upload.filename)
            for upload in uploads
            if not _allowed_file(upload.filename, app.config["ALLOWED_EXTENSIONS"])
        ]
        if unsupported:
            return jsonify(
                error="Định dạng chưa được hỗ trợ. Chỉ nhận PDF, PNG, JPG hoặc JPEG.",
                files=unsupported,
            ), 400

        if len(uploads) > 1:
            return _extract_batch(app, uploads)

        upload = uploads[0]
        if upload.content_length and upload.content_length > app.config["MAX_FILE_SIZE"]:
            return jsonify(error="Mỗi tệp chỉ được tối đa 12 MB."), 413

        extension = upload.filename.rsplit(".", 1)[1].lower()
        filepath = Path(app.config["UPLOAD_FOLDER"]) / f"{uuid4().hex}.{extension}"

        try:
            _save_upload_with_limit(upload, filepath, app.config["MAX_FILE_SIZE"])
            if extension == "pdf":
                processed = process_pdf(
                    filepath,
                    max_pages=app.config["MAX_PDF_PAGES"],
                    tesseract_cmd=app.config.get("TESSERACT_CMD"),
                    max_workers=app.config["OCR_MAX_WORKERS"],
                )
            else:
                processed = ocr_image(
                    filepath,
                    tesseract_cmd=app.config.get("TESSERACT_CMD"),
                    max_workers=app.config["OCR_MAX_WORKERS"],
                )
            extracted = extract_personal_information(
                processed.text,
                page_texts=processed.pages,
                cccd_mrz_texts=processed.mrz_texts,
            )
            _enhance_with_ai(
                app,
                filepath,
                extension,
                processed,
                extracted,
                consented=_ai_requested(),
            )
        except OCRUnavailableError as error:
            return jsonify(
                error="Không thể OCR tệp quét trên máy chủ này.",
                detail=str(error),
                code="ocr_unavailable",
            ), 503
        except UploadTooLargeError:
            return jsonify(error="Mỗi tệp chỉ được tối đa 12 MB."), 413
        except (PDFProcessingError, OSError, ValueError) as error:
            return jsonify(error="Không thể đọc tệp đã tải lên.", detail=str(error)), 422
        finally:
            # Tệp nguồn chứa dữ liệu cá nhân chỉ tồn tại trong lúc xử lý yêu cầu.
            filepath.unlink(missing_ok=True)

        validation = validate_form_data(extracted.fields, required_fields=())
        return jsonify(
            fields=extracted.fields,
            field_confidence=extracted.confidence,
            warnings=[*processed.warnings, *extracted.warnings, *validation.warnings],
            source=processed.source,
            processed_file_count=1,
            processed_files=[
                {
                    "name": _display_filename(upload.filename),
                    "status": "processed",
                    "source": processed.source,
                    "page_count": len(processed.pages),
                    "extracted_fields": [field for field, value in extracted.fields.items() if value],
                }
            ],
            # Giúp người dân đối chiếu; giao diện không lưu nội dung này sau khi tải lại.
            raw_text=processed.text[:12000],
        )

    @app.post("/api/validate")
    def validate():
        payload = request.get_json(silent=True) or {}
        template_id = payload.get("template_id", "tro_cap_huu_tri")
        template = get_template(template_id)
        if template is None:
            return jsonify(error="Biểu mẫu không tồn tại."), 404
        validation = validate_form_data(payload.get("fields", {}), required_fields=template.required_fields)
        return jsonify(valid=validation.valid, errors=validation.errors, warnings=validation.warnings)

    @app.post("/api/interview/start")
    def start_interview():
        payload = request.get_json(silent=True) or {}
        template = get_template(payload.get("template_id", "tro_cap_huu_tri"))
        if template is None:
            return jsonify(error="Biểu mẫu không tồn tại."), 404
        return jsonify(app.extensions["interview_service"].start(template))

    @app.post("/api/interview/answer")
    def answer_interview():
        payload = request.get_json(silent=True) or {}
        session_id = payload.get("session_id", "")
        try:
            response = app.extensions["interview_service"].answer(session_id, str(payload.get("answer", "")))
        except InterviewNotFoundError as error:
            return jsonify(error=str(error), code="interview_expired"), 410
        return jsonify(response)

    @app.delete("/api/interview/<session_id>")
    def discard_interview(session_id: str):
        app.extensions["interview_service"].discard(session_id)
        return "", 204

    @app.post("/api/generate")
    def generate():
        payload = request.get_json(silent=True) or {}
        template_id = payload.get("template_id", "tro_cap_huu_tri")
        template = get_template(template_id)
        if template is None:
            return jsonify(error="Biểu mẫu không tồn tại."), 404

        fields = payload.get("fields", {})
        validation = validate_form_data(fields, required_fields=template.required_fields)
        if not validation.valid:
            return jsonify(error="Vui lòng hoàn thiện các trường bắt buộc.", errors=validation.errors), 422

        output_path = Path(app.config["OUTPUT_FOLDER"]) / f"{template_id}_{uuid4().hex}.docx"
        try:
            generate_document(template, fields, output_path)
        except DocumentGenerationError as error:
            return jsonify(error="Không thể tạo tệp Word.", detail=str(error)), 500

        # Đọc vào bộ nhớ rồi xóa ngay: tệp chứa dữ liệu cá nhân không phụ thuộc
        # vào việc trình duyệt đóng kết nối tải xuống để được dọn dẹp.
        try:
            download_buffer = io.BytesIO(output_path.read_bytes())
        finally:
            output_path.unlink(missing_ok=True)

        return send_file(
            download_buffer,
            as_attachment=True,
            download_name=f"{template.download_name}.docx",
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    @app.errorhandler(RequestEntityTooLarge)
    def file_too_large(_error):
        maximum_batch_size = app.config["MAX_BATCH_TOTAL_SIZE"] // (1024 * 1024)
        return jsonify(error=f"Tổng dung lượng các tệp vượt quá {maximum_batch_size} MB."), 413

    return app


app = create_app()


if __name__ == "__main__":
    # threaded=True để một yêu cầu OCR/trích xuất kéo dài vài giây không chặn
    # các yêu cầu khác (vd. /health, tài nguyên tĩnh, hoặc một tab trình duyệt
    # khác) trên máy chủ phát triển đơn luồng mặc định của Flask.
    app.run(debug=True, threaded=True)
