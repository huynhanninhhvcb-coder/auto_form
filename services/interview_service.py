"""Trợ lý phỏng vấn cục bộ để điền đơn khi người dân không có tệp nguồn.

Đây là hội thoại có cấu trúc: câu hỏi được xác định từ trường của biểu mẫu và
câu trả lời được kiểm tra ngay. Dữ liệu chỉ giữ trong RAM tối đa 30 phút, không
gửi tới nhà cung cấp AI bên ngoài và không ghi vào cơ sở dữ liệu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import secrets
import threading
import time
import unicodedata

from services.extraction_service import FIELD_NAMES
from services.template_service import FormTemplate
from services.validation_service import validate_form_data
from utils.text_utils import normalize_citizen_id, normalize_date, normalize_phone, normalize_text


@dataclass(frozen=True)
class InterviewStep:
    field: str
    question: str
    required: bool = False


_TRO_CAP_HUU_TRI_STEPS: tuple[InterviewStep, ...] = (
    InterviewStep("full_name", "Vui lòng cho biết họ và tên đầy đủ của bạn, đúng như trên CCCD.", True),
    InterviewStep("date_of_birth", "Ngày, tháng, năm sinh của bạn là gì? Ví dụ: 30/05/1951.", True),
    InterviewStep("gender", "Giới tính của bạn là Nam, Nữ hay Khác?"),
    InterviewStep("ethnic_group", "Dân tộc của bạn là gì?"),
    InterviewStep("citizen_id", "Vui lòng đọc từng chữ số CCCD hoặc số định danh cá nhân, gồm 12 số. Ví dụ: không, bảy, chín…", True),
    InterviewStep("residence_address", "Địa chỉ nơi cư trú hiện nay của bạn là gì? Vui lòng nêu đầy đủ số nhà, đường, phường/xã, quận/huyện, tỉnh/thành phố.", True),
    InterviewStep("contact_address", "Địa chỉ liên lạc có giống nơi cư trú không? Nếu giống, trả lời “giống nơi cư trú”; nếu khác, hãy nêu địa chỉ liên lạc."),
    InterviewStep("phone_number", "Số điện thoại để cơ quan liên hệ với bạn là gì? Hãy đọc từng chữ số.",),
    InterviewStep("benefit_receiving_location", "Bạn muốn nhận trợ cấp tại đâu?"),
    InterviewStep("bank_account_name", "Tên chủ tài khoản ngân hàng để nhận trợ cấp là gì?"),
    InterviewStep("bank_account_number", "Số tài khoản ngân hàng là gì?"),
    InterviewStep("bank_name", "Tên ngân hàng là gì?"),
    InterviewStep("guardian_full_name", "Nếu có người giám hộ hoặc được ủy quyền, hãy cho biết họ tên người đó."),
    InterviewStep("guardian_date_of_birth", "Ngày sinh của người giám hộ/ủy quyền là gì?"),
    InterviewStep("guardian_citizen_id", "Số CCCD của người giám hộ/ủy quyền là gì? Hãy đọc từng chữ số."),
    InterviewStep("guardian_address", "Địa chỉ của người giám hộ/ủy quyền là gì?"),
    InterviewStep("guardian_phone", "Số điện thoại của người giám hộ/ủy quyền là gì?"),
    InterviewStep("guardian_relationship", "Người đó có quan hệ gì với bạn?"),
)

# Các bước dùng chung cho người đề nghị ở hai đơn hỏa táng/mai táng: giống hệt
# 4 câu đầu của mẫu trợ cấp hưu trí (không hỏi giới tính/dân tộc vì hai mẫu
# này không cần), cộng thêm số điện thoại.
_NGUOI_DE_NGHI_STEPS: tuple[InterviewStep, ...] = (
    InterviewStep("full_name", "Vui lòng cho biết họ và tên đầy đủ của bạn, đúng như trên CCCD.", True),
    InterviewStep("date_of_birth", "Ngày, tháng, năm sinh của bạn là gì? Ví dụ: 30/05/1951.", True),
    InterviewStep("citizen_id", "Vui lòng đọc từng chữ số CCCD hoặc số định danh cá nhân, gồm 12 số. Ví dụ: không, bảy, chín…", True),
    InterviewStep("residence_address", "Nơi cư trú hiện nay của bạn ở đâu? Vui lòng nêu đầy đủ số nhà, đường, phường/xã, quận/huyện, tỉnh/thành phố.", True),
    InterviewStep("phone_number", "Số điện thoại để cơ quan liên hệ với bạn là gì? Hãy đọc từng chữ số."),
)

_BANK_ACCOUNT_STEPS: tuple[InterviewStep, ...] = (
    InterviewStep("bank_account_name", "Tên chủ tài khoản ngân hàng để nhận hỗ trợ là gì?"),
    InterviewStep("bank_account_number", "Số tài khoản ngân hàng là gì?"),
    InterviewStep("bank_name", "Tên ngân hàng là gì?"),
)

_HO_TRO_HOA_TANG_STEPS: tuple[InterviewStep, ...] = (
    *_NGUOI_DE_NGHI_STEPS,
    InterviewStep("deceased_relationship", "Bạn có quan hệ gì với người đã mất?"),
    InterviewStep("deceased_full_name", "Họ và tên đầy đủ của người đã mất là gì?", True),
    InterviewStep("deceased_death_date", "Người đã mất qua đời vào ngày, tháng, năm nào? Ví dụ: 15/08/2026.", True),
    InterviewStep("death_certificate_number", "Số giấy chứng tử là bao nhiêu, nếu bạn có?"),
    InterviewStep("death_certificate_date", "Giấy chứng tử được cấp vào ngày, tháng, năm nào?"),
    InterviewStep("death_certificate_issuer", "Giấy chứng tử do cơ quan nào cấp?"),
    InterviewStep(
        "org_name",
        "Nếu bạn đại diện cho một tổ chức đứng ra lo việc hỏa táng, hãy cho biết tên tổ chức đó. Nếu không, trả lời “bỏ qua”.",
    ),
    *_BANK_ACCOUNT_STEPS,
)

_HO_TRO_MAI_TANG_STEPS: tuple[InterviewStep, ...] = (
    *_NGUOI_DE_NGHI_STEPS,
    InterviewStep("deceased_relationship", "Bạn có quan hệ gì với người đã mất?"),
    InterviewStep("deceased_full_name", "Họ và tên đầy đủ của người đã mất là gì?", True),
    InterviewStep("deceased_date_of_birth", "Ngày, tháng, năm sinh của người đã mất là gì?"),
    InterviewStep("deceased_gender", "Giới tính của người đã mất là Nam hay Nữ?"),
    InterviewStep("deceased_ethnic_group", "Dân tộc của người đã mất là gì?"),
    InterviewStep("deceased_nationality", "Quốc tịch của người đã mất là gì? Nếu là Việt Nam, trả lời “Việt Nam”."),
    InterviewStep("deceased_residence_address", "Nơi cư trú của người đã mất ở đâu?"),
    InterviewStep(
        "deceased_citizen_id",
        "Số CCCD hoặc số định danh cá nhân của người đã mất là gì? Vui lòng đọc từng chữ số, gồm 12 số.",
    ),
    InterviewStep("deceased_death_date", "Người đã mất qua đời vào ngày, tháng, năm nào? Ví dụ: 15/08/2026.", True),
    InterviewStep("deceased_death_place", "Người đó qua đời ở đâu?"),
    InterviewStep("deceased_death_cause", "Nguyên nhân qua đời là gì, nếu bạn biết?"),
    InterviewStep("death_certificate_number", "Số giấy báo tử hoặc giấy tờ thay thế là bao nhiêu, nếu bạn có?"),
    InterviewStep("death_certificate_date", "Giấy báo tử được cấp vào ngày, tháng, năm nào?"),
    InterviewStep("death_certificate_issuer", "Giấy báo tử do cơ quan nào cấp?"),
    *_BANK_ACCOUNT_STEPS,
    InterviewStep(
        "org_name",
        "Nếu có cơ quan, tổ chức đứng ra tổ chức mai táng thay vì cá nhân, hãy cho biết tên tổ chức đó. Nếu không, trả lời “bỏ qua”.",
    ),
    InterviewStep("org_address", "Địa chỉ của tổ chức đó là gì?"),
    InterviewStep("org_representative_name", "Người đại diện theo pháp luật của tổ chức là ai?"),
    InterviewStep("org_representative_title", "Chức vụ của người đại diện đó là gì?"),
    InterviewStep("org_phone", "Số điện thoại của tổ chức là gì?"),
)

_HO_TRO_NGHI_QUYET_STEPS: tuple[InterviewStep, ...] = (
    InterviewStep("full_name", "Vui lòng cho biết họ và tên đầy đủ của bạn, đúng như trên CCCD.", True),
    InterviewStep("date_of_birth", "Ngày, tháng, năm sinh của bạn là gì? Ví dụ: 30/05/1990.", True),
    InterviewStep("citizen_id", "Vui lòng đọc từng chữ số CCCD, gồm đúng 12 số.", True),
    InterviewStep("citizen_id_issue_date", "CCCD của bạn được cấp ngày, tháng, năm nào?", True),
    InterviewStep("citizen_id_issue_place", "CCCD của bạn do cơ quan nào cấp?", True),
    InterviewStep(
        "residence_address",
        "Hộ khẩu thường trú của bạn ở đâu? Hãy nêu đầy đủ số nhà, đường, phường xã, quận huyện và tỉnh thành phố.",
        True,
    ),
    InterviewStep("temporary_address", "Nơi tạm trú hiện nay của bạn ở đâu? Nếu không có, hãy trả lời bỏ qua."),
    InterviewStep("phone_number", "Số điện thoại liên hệ của bạn là gì? Hãy đọc từng chữ số."),
    InterviewStep("occupation", "Nghề nghiệp hiện nay của bạn là gì?"),
    InterviewStep("employer", "Đơn vị công tác của bạn là gì? Nếu không có, hãy trả lời bỏ qua."),
    InterviewStep(
        "support_category",
        "Bạn thuộc nhóm hỗ trợ nào? Hãy trả lời một trong năm nhóm: phụ nữ sinh đủ hai con trước 35 tuổi; hộ nghèo; hộ cận nghèo; đối tượng bảo trợ xã hội; hoặc đối tượng sống tại xã đảo.",
        True,
    ),
    InterviewStep(
        "support_detail",
        "Nếu thuộc hộ nghèo hoặc hộ cận nghèo, hãy cho biết mã số. Nếu thuộc diện bảo trợ xã hội hoặc sống tại xã đảo, hãy nêu thông tin cụ thể. Trường hợp khác có thể trả lời bỏ qua.",
    ),
)

TEMPLATE_STEPS: dict[str, tuple[InterviewStep, ...]] = {
    "tro_cap_huu_tri": _TRO_CAP_HUU_TRI_STEPS,
    "ho_tro_hoa_tang": _HO_TRO_HOA_TANG_STEPS,
    "ho_tro_mai_tang": _HO_TRO_MAI_TANG_STEPS,
    "ho_tro_nq40": _HO_TRO_NGHI_QUYET_STEPS,
    "ho_tro_nq32": _HO_TRO_NGHI_QUYET_STEPS,
}

SKIP_ANSWERS = {"bo qua", "khong co", "khong nho", "khong ap dung", "skip"}
SPOKEN_DIGITS = {
    "khong": "0",
    "linh": "0",
    "mot": "1",
    "hai": "2",
    "ba": "3",
    "bon": "4",
    "tu": "4",
    "nam": "5",
    "lam": "5",
    "sau": "6",
    "bay": "7",
    "tam": "8",
    "chin": "9",
}
CARDINAL_DIGITS = {key: int(value) for key, value in SPOKEN_DIGITS.items() if key != "linh"}


def _fold_answer(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(character for character in decomposed if unicodedata.category(character) != "Mn").replace("đ", "d").replace("Đ", "D")


def _spoken_digits(value: str) -> str:
    """Chuyển cách đọc từng số tiếng Việt từ SpeechRecognition thành chữ số."""
    tokens = re.findall(r"\d+|[a-z]+", _fold_answer(value).lower())
    digits: list[str] = []
    for token in tokens:
        if token.isdigit():
            digits.append(token)
        elif token in SPOKEN_DIGITS:
            digits.append(SPOKEN_DIGITS[token])
    return "".join(digits)


def _spoken_number(value: str, *, year: bool = False) -> int | None:
    """Đọc một số tiếng Việt cơ bản, đủ cho ngày/tháng/năm trong phỏng vấn."""
    tokens = re.findall(r"\d+|[a-z]+", _fold_answer(value).lower())
    if not tokens:
        return None
    if all(token.isdigit() for token in tokens):
        return int("".join(tokens))
    if year and len(tokens) >= 3 and all(token in CARDINAL_DIGITS for token in tokens):
        return int("".join(str(CARDINAL_DIGITS[token]) for token in tokens))

    def under_one_hundred(items: list[str]) -> int | None:
        if not items:
            return 0
        if "muoi" in items:
            index = items.index("muoi")
            tens = CARDINAL_DIGITS.get(items[index - 1], 1) if index else 1
            if index and items[index - 1] not in CARDINAL_DIGITS:
                return None
            result = tens * 10
            if index + 1 < len(items) and items[index + 1] in CARDINAL_DIGITS:
                result += CARDINAL_DIGITS[items[index + 1]]
            return result
        if len(items) == 1 and items[0] in CARDINAL_DIGITS:
            return CARDINAL_DIGITS[items[0]]
        return None

    def under_one_thousand(items: list[str]) -> int | None:
        if "tram" not in items:
            return under_one_hundred(items)
        index = items.index("tram")
        if index == 0 or items[index - 1] not in CARDINAL_DIGITS:
            return None
        tail = [item for item in items[index + 1 :] if item not in {"le", "linh", "va"}]
        remainder = under_one_hundred(tail)
        return CARDINAL_DIGITS[items[index - 1]] * 100 + (remainder if remainder is not None else 0)

    if "nghin" in tokens:
        index = tokens.index("nghin")
        thousands = under_one_thousand(tokens[:index])
        remainder = under_one_thousand(tokens[index + 1 :])
        if thousands is None or remainder is None:
            return None
        return thousands * 1000 + remainder
    return under_one_thousand(tokens)


def _normalise_spoken_date(value: str) -> str:
    direct = normalize_date(value)
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", direct):
        return direct
    folded = _fold_answer(value).lower()
    match = re.search(r"(?:ngay\s+)?(.+?)\s+thang\s+(.+?)\s+nam\s+(.+)$", folded)
    if not match:
        return direct
    day = _spoken_number(match.group(1))
    month = _spoken_number(match.group(2))
    year = _spoken_number(match.group(3), year=True)
    if day is None or month is None or year is None:
        return direct
    return f"{day:02d}/{month:02d}/{year:04d}"


class InterviewNotFoundError(ValueError):
    pass


@dataclass
class InterviewSession:
    template: FormTemplate
    fields: dict[str, str] = field(default_factory=lambda: {key: "" for key in FIELD_NAMES})
    step_index: int = 0
    expires_at: float = 0.0
    steps: tuple[InterviewStep, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.steps:
            self.steps = TEMPLATE_STEPS.get(self.template.id, _TRO_CAP_HUU_TRI_STEPS)


class InterviewService:
    def __init__(self, ttl_seconds: int = 30 * 60):
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, InterviewSession] = {}
        self._lock = threading.Lock()

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [key for key, session in self._sessions.items() if session.expires_at <= now]
        for key in expired:
            self._sessions.pop(key, None)

    def _session(self, session_id: str) -> InterviewSession:
        self._purge_expired()
        session = self._sessions.get(session_id)
        if session is None:
            raise InterviewNotFoundError("Phiên phỏng vấn không còn hiệu lực. Vui lòng bắt đầu lại.")
        session.expires_at = time.monotonic() + self.ttl_seconds
        return session

    @staticmethod
    def _is_skip(answer: str) -> bool:
        folded = "".join(character for character in _fold_answer(answer).lower() if character.isalnum() or character.isspace())
        return normalize_text(folded) in SKIP_ANSWERS

    @staticmethod
    def _normalise_answer(field: str, answer: str, known_fields: dict[str, str]) -> tuple[str, str | None]:
        value = normalize_text(answer)
        folded = _fold_answer(value).lower()
        if field in {"citizen_id", "guardian_citizen_id", "deceased_citizen_id"}:
            value = normalize_citizen_id(_spoken_digits(value) or value)
            if not re.fullmatch(r"\d{12}", value):
                return "", "Số CCCD/số định danh phải gồm đúng 12 chữ số. Vui lòng đọc lại từng số."
        elif field in {"date_of_birth", "citizen_id_issue_date", "guardian_date_of_birth", "deceased_date_of_birth", "deceased_death_date", "death_certificate_date"}:
            value = _normalise_spoken_date(value)
            validation = validate_form_data({field: value}, ())
            if field in validation.errors:
                return "", validation.errors[field]
        elif field in {"phone_number", "guardian_phone", "org_phone"}:
            value = normalize_phone(_spoken_digits(value) or value)
            if value and not re.fullmatch(r"0\d{9}", value):
                return "", "Số điện thoại cần gồm 10 chữ số và bắt đầu bằng số 0."
        elif field == "bank_account_number":
            value = _spoken_digits(value) or value
        elif field in {"gender", "deceased_gender"}:
            if re.search(r"\bnu\b", folded):
                value = "Nữ"
            elif re.search(r"\bnam\b", folded):
                value = "Nam"
            elif "khac" in folded:
                value = "Khác"
            else:
                return "", "Vui lòng trả lời Nam, Nữ hoặc Khác."
        elif field == "support_category":
            categories = (
                (r"(?:hai|2)\s*con|truoc\s*(?:ba\s*muoi\s*lam|35)", "Phụ nữ sinh đủ hai con trước 35 tuổi"),
                (r"can\s*ngheo", "Hộ cận nghèo"),
                (r"ho\s*ngheo|ngheo", "Hộ nghèo"),
                (r"bao\s*tro\s*xa\s*hoi", "Đối tượng bảo trợ xã hội"),
                (r"xa\s*dao|song\s*tai\s*dao", "Đối tượng sống tại xã đảo"),
            )
            value = next((label for pattern, label in categories if re.search(pattern, folded)), "")
            if not value:
                return "", "Vui lòng chọn một trong năm nhóm hỗ trợ vừa được nêu trong câu hỏi."
        elif field == "contact_address" and "giong noi cu tru" in folded:
            value = known_fields.get("residence_address", "")
            if not value:
                return "", "Tôi chưa có địa chỉ nơi cư trú. Vui lòng cung cấp địa chỉ liên lạc cụ thể."
        elif field in {"full_name", "deceased_full_name"} and len(re.sub(r"[^A-Za-zÀ-ỹĐđ]", "", value)) < 4:
            return "", "Họ và tên có vẻ chưa đầy đủ. Vui lòng cho biết đầy đủ họ, chữ đệm và tên."
        return value, None

    @staticmethod
    def _response(session_id: str, session: InterviewSession, message: str, error: str | None = None) -> dict:
        complete = session.step_index >= len(session.steps)
        step = None if complete else session.steps[session.step_index]
        return {
            "session_id": session_id,
            "complete": complete,
            "field": step.field if step else None,
            "question": step.question if step else None,
            "message": message,
            "error": error,
            "fields": session.fields.copy(),
            "progress": {"current": min(session.step_index + 1, len(session.steps)), "total": len(session.steps)},
        }

    def start(self, template: FormTemplate) -> dict:
        with self._lock:
            self._purge_expired()
            session_id = secrets.token_urlsafe(24)
            session = InterviewSession(template=template, expires_at=time.monotonic() + self.ttl_seconds)
            self._sessions[session_id] = session
            return self._response(session_id, session, "Xin chào. Tôi sẽ hỏi lần lượt để hỗ trợ điền đơn. Bạn có thể trả lời “bỏ qua” cho trường không bắt buộc.")

    def answer(self, session_id: str, answer: str) -> dict:
        with self._lock:
            session = self._session(session_id)
            if session.step_index >= len(session.steps):
                return self._response(session_id, session, "Cuộc phỏng vấn đã hoàn tất. Hãy kiểm tra lại thông tin trước khi tạo đơn.")
            step = session.steps[session.step_index]
            answer = normalize_text(answer)
            if not answer:
                return self._response(session_id, session, "Tôi chưa nhận được câu trả lời. Vui lòng trả lời câu hỏi này.", "Câu trả lời không được để trống.")
            if self._is_skip(answer):
                if step.required:
                    return self._response(session_id, session, "Trường này bắt buộc để tạo đơn. Vui lòng cung cấp thông tin.", "Không thể bỏ qua trường bắt buộc.")
                value = ""
            else:
                value, error = self._normalise_answer(step.field, answer, session.fields)
                if error:
                    return self._response(session_id, session, error, error)

            session.fields[step.field] = value
            session.step_index += 1
            if session.step_index >= len(session.steps):
                validation = validate_form_data(session.fields, session.template.required_fields)
                if not validation.valid:
                    return self._response(session_id, session, "Đã thu thập xong. Một vài trường bắt buộc cần được rà soát ở bước tiếp theo.")
                return self._response(session_id, session, "Đã thu thập đủ thông tin. Tôi đã điền dữ liệu vào biểu mẫu để bạn kiểm tra lần cuối.")
            next_step = session.steps[session.step_index]
            value_message = "Đã bỏ qua trường không bắt buộc." if not value else f"Đã ghi nhận {FIELD_NAMES[step.field]}."
            return self._response(session_id, session, f"{value_message} {next_step.question}")

    def discard(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
