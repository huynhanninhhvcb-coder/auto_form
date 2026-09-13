"""Chatbot hướng dẫn thủ tục hành chính: tìm thủ tục theo câu hỏi tự do và
trả lời đúng trọng tâm (hồ sơ, phí, thời gian...) từ các file .txt cục bộ.

Được chuyển từ dự án chatbot_hanhchinh độc lập (giữ nguyên logic suy luận)
vào làm một service của Auto Form. Khác biệt chính: đường dẫn thư mục dữ
liệu được truyền vào theo tham số thay vì cố định toàn cục, và việc ghi/xoá
ngữ cảnh hội thoại (session) được tách ra ngoài — hàm ở đây chỉ mô tả hành
động cần làm (``context_action``/``context_value``) để app.py áp dụng lên
Flask session, giữ module này không phụ thuộc Flask nên có thể kiểm thử độc lập.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import difflib
import re
import unicodedata

import requests


GOOGLE_DRIVE_API_URL = "https://www.googleapis.com/drive/v3/files"

# Các trường thông tin mà một thủ tục có thể có. Mỗi trường gồm:
# (khóa, emoji, nhãn hiển thị, regex nhận diện dòng tiêu đề trong file .txt)
CATEGORY_META = [
    ('doi_tuong', '👥', 'Đối tượng áp dụng',
        r'Đối\s+tượng(?:\s+(?:làm\s+hồ\s+sơ|áp\s+dụng|được\s+hưởng|thụ\s+hưởng))?'),
    ('ho_so', '📋', 'Thành phần hồ sơ',
        r'(?:Thành\s+phần\s+hồ\s+sơ|Hồ\s+sơ\s+(?:gồm|bao\s+gồm)|Giấy\s+tờ\s+cần\s+(?:nộp|chuẩn\s+bị))'),
    ('co_quan', '🏢', 'Cơ quan giải quyết',
        r'Cơ\s+quan(?:\s+(?:giải\s+quyết|thực\s+hiện|có\s+thẩm\s+quyền))?'),
    ('thoi_gian', '⏰', 'Thời gian giải quyết',
        r'Thời\s+(?:gian|hạn)(?:\s+giải\s+quyết)?'),
    ('phi', '💰', 'Lệ phí',
        r'(?:Lệ\s+phí|Mức\s+phí|Phí\s+thực\s+hiện)'),
    ('cac_buoc', '🔰', 'Các bước thực hiện',
        r'(?:Các\s+bước(?:\s+thực\s+hiện)?|Trình\s+tự(?:\s+thực\s+hiện)?|Quy\s+trình(?:\s+thực\s+hiện)?)'),
    ('dia_chi', '📍', 'Địa chỉ tiếp nhận',
        r'Địa\s+chỉ(?:\s+tiếp\s+nhận)?'),
    ('ghi_chu', '💡', 'Lưu ý',
        r'(?:Lưu\s+ý|Ghi\s+chú)'),
]
CATEGORY_EMOJI = {k: e for k, e, _l, _p in CATEGORY_META}
CATEGORY_LABEL = {k: l for k, _e, l, _p in CATEGORY_META}

# Từ khóa nhận diện "ý định" của câu hỏi (đã chuẩn hóa: chữ thường, không dấu)
INTENT_KEYWORDS = {
    'ho_so': ['ho so can', 'ho so gom', 'can ho so', 'giay to gi', 'giay to can', 'can giay to',
              'chuan bi giay to', 'chuan bi ho so', 'can nhung giay to', 'thanh phan ho so'],
    'dia_chi': ['nop o dau', 'nop tai dau', 'noi nop', 'noi tiep nhan', 'dia chi tiep nhan',
                'dia chi nop', 'den dau de nop'],
    'phi': ['le phi', 'mat phi khong', 'co mat phi', 'bao nhieu tien', 'dong bao nhieu',
            'phi la bao nhieu', 'co phai dong tien', 'phi bao nhieu', 'mat bao nhieu', 'gia bao nhieu'],
    'thoi_gian': ['bao lau', 'may ngay', 'thoi gian giai quyet', 'thoi han giai quyet',
                  'khi nao co ket qua', 'bao nhieu ngay'],
    'doi_tuong': ['ai duoc', 'doi tuong nao', 'dieu kien gi', 'ai duoc huong',
                  'co du dieu kien', 'dieu kien duoc'],
    'co_quan': ['co quan nao giai quyet', 'ai giai quyet', 'noi giai quyet', 'co quan nao'],
    'cac_buoc': ['cac buoc', 'quy trinh', 'trinh tu thuc hien', 'lam nhu the nao', 'thuc hien ra sao',
                 'thu tuc the nao'],
    'ghi_chu': ['can luu y', 'luu y gi', 'ghi chu gi', 'co gi can luu y'],
}


@dataclass(frozen=True)
class ChatAnswer:
    reply: str
    suggestions: list[str] | None = None
    # Hành động cần áp dụng lên session ở tầng Flask: "set" (lưu context_value
    # làm thủ tục đang hỏi dở), "clear" (quên ngữ cảnh) hoặc None (giữ nguyên).
    context_action: str | None = None
    context_value: str | None = None
    # True khi câu hỏi không khớp thủ tục cục bộ nào: tầng Flask có thể đề
    # nghị người dùng bấm xác nhận gửi câu hỏi này cho ChatGPT trả lời bổ
    # sung (xem services/chatbot_ai_service.py). Không tự động gửi vì đó là
    # dữ liệu ra ngoài ứng dụng, cần người dùng đồng ý cho từng câu hỏi.
    offer_ai: bool = False


def remove_accents(text: str) -> str:
    """Bỏ dấu tiếng Việt.

    Chữ "đ/Đ" phải đổi thành "d/D" TRƯỚC khi chuẩn hóa NFD, vì đây là một ký
    tự Unicode riêng biệt (không phải "d" + dấu phụ) nên NFD không tách được
    - nếu không xử lý riêng, encode ascii sẽ xóa mất "đ".
    """
    text = text.replace('đ', 'd').replace('Đ', 'D')
    text = unicodedata.normalize('NFD', text)
    text = text.encode('ascii', 'ignore').decode('utf-8')
    return text.lower()


def chuan_hoa_tim_kiem(text: str) -> str:
    """Chuẩn hóa văn bản tìm kiếm."""
    text = remove_accents(text)
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = ' '.join(text.split())
    return text


def co_tu_khoa(cau_hoi_chuan: str, tu_khoa_list: Iterable[str]) -> bool:
    """Kiểm tra câu hỏi có chứa một trong các từ khóa hay không.

    Từ khóa 1 từ được so khớp theo TỪ NGUYÊN VẸN (tránh việc từ ngắn như "hi"
    khớp nhầm vào bên trong từ khác, ví dụ "phí"/"chi" sau khi bỏ dấu đều
    chứa chuỗi con "hi"). Cụm nhiều từ vẫn so khớp theo chuỗi con.
    """
    words = cau_hoi_chuan.split()
    for tu in tu_khoa_list:
        if ' ' in tu:
            if tu in cau_hoi_chuan:
                return True
        elif tu in words:
            return True
    return False


def detect_intent(cau_hoi_chuan: str) -> str | None:
    """Đoán người dùng đang hỏi cụ thể về trường thông tin nào (hồ sơ, phí, thời gian...)"""
    for key, phrases in INTENT_KEYWORDS.items():
        for p in phrases:
            if p in cau_hoi_chuan:
                return key
    return None


def get_ten_thutuc_from_file(thutuc_folder: Path, filename: str) -> str:
    """Đọc tên thủ tục từ file.

    Ưu tiên:
    1. Dòng TÊN THỦ TỤC: (nếu có)
    2. Dòng đầu tiên (loại bỏ số thứ tự và từ "Thủ tục")
    3. Tên file (fallback)
    """
    file_path = Path(thutuc_folder) / filename
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            for line in lines:
                if line.startswith('TÊN THỦ TỤC:'):
                    return line.replace('TÊN THỦ TỤC:', '').strip()
            if lines:
                first_line = lines[0].strip()
                first_line = re.sub(r'^\d+\.\s*', '', first_line)
                first_line = re.sub(r'^Thủ tục\s*', '', first_line)
                first_line = re.sub(r'^Thủ\s+tục\s*', '', first_line)
                if first_line:
                    return first_line
    except OSError:
        pass
    name = filename.replace('.txt', '').replace('_', ' ')
    return name.title()


def parse_sections(noi_dung_goc: str) -> dict[str, str]:
    """Tách nội dung file thủ tục thành các mục có cấu trúc (đối tượng, hồ sơ,
    cơ quan, thời gian, lệ phí, các bước, địa chỉ, lưu ý), bất kể file có
    dùng dấu ":" sau tiêu đề hay không, để có thể trả lời riêng từng mục.
    """
    lines = noi_dung_goc.split('\n')
    blocks = []
    current: list[str] = []
    for line in lines:
        if re.match(r'^\s*\d+\.\s', line) and current:
            blocks.append('\n'.join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append('\n'.join(current))
    blocks = [b for b in blocks if b.strip()]

    sections: dict[str, str] = {}
    for block in blocks[1:]:  # blocks[0] là dòng tên thủ tục
        first_line, _, rest_block = block.partition('\n')
        header_body = re.sub(r'^\s*\d+\.\s*', '', first_line)
        for key, _emoji, _label, pattern in CATEGORY_META:
            m = re.match(pattern, header_body, re.IGNORECASE)
            if not m:
                continue
            rest_first_line = header_body[m.end():].lstrip(':').strip()
            rest_first_line = re.sub(r'^(?:bao\s+gồm|gồm)\s*:?\s*', '', rest_first_line, flags=re.IGNORECASE)
            content = '\n'.join(p for p in [rest_first_line, rest_block.strip()] if p).strip()
            if content:
                sections[key] = content
            break
    return sections


def list_thutuc_files(thutuc_folder: Path) -> list[str]:
    thutuc_folder = Path(thutuc_folder)
    if not thutuc_folder.exists():
        thutuc_folder.mkdir(parents=True, exist_ok=True)
        return []
    return sorted(f.name for f in thutuc_folder.iterdir() if f.suffix == '.txt' and not f.name.startswith('_'))


def list_thutuc_names(thutuc_folder: Path) -> list[tuple[str, str]]:
    return [(f, get_ten_thutuc_from_file(thutuc_folder, f)) for f in list_thutuc_files(thutuc_folder)]


def load_thutuc_from_file(thutuc_folder: Path, filename: str) -> dict | None:
    """Đọc nội dung file thủ tục, trả về dict chứa nội dung gốc và các mục đã tách"""
    file_path = Path(thutuc_folder) / filename
    if not file_path.exists():
        return None
    noi_dung_goc = file_path.read_text(encoding='utf-8')
    return {
        'filename': filename,
        'ten': get_ten_thutuc_from_file(thutuc_folder, filename),
        'noi_dung_goc': noi_dung_goc,
        'sections': parse_sections(noi_dung_goc),
    }


def tim_kiem_thutuc(thutuc_folder: Path, cau_hoi: str) -> tuple[dict | None, int]:
    """Tìm kiếm thủ tục từ câu hỏi (hỗ trợ tìm kiếm mờ + dung sai lỗi chính tả).

    Trả về (thutuc, điểm số khớp) hoặc (None, 0) nếu không đạt ngưỡng tối thiểu.
    """
    files = list_thutuc_files(thutuc_folder)
    if not files:
        return None, 0

    cau_hoi_chuan = chuan_hoa_tim_kiem(cau_hoi)
    best_match = None
    best_score = 0

    for file in files:
        ten_thutuc = get_ten_thutuc_from_file(thutuc_folder, file)
        ten_chuan = chuan_hoa_tim_kiem(ten_thutuc)

        # 1. Khớp chính xác (điểm cao nhất)
        if ten_chuan == cau_hoi_chuan:
            return load_thutuc_from_file(thutuc_folder, file), 100

        score = 0
        # 2. Tên thủ tục nằm trong câu hỏi hoặc ngược lại
        if len(ten_chuan) > 5 and ten_chuan in cau_hoi_chuan:
            score = 60
        elif len(cau_hoi_chuan) > 3 and cau_hoi_chuan in ten_chuan:
            score = 50
        else:
            # 3. Tìm kiếm mờ: tính điểm dựa trên số từ khóa trùng có nghĩa
            #    (bỏ qua từ quá ngắn như "la", "gi", "o" vì dễ trùng ngẫu nhiên
            #    và gây khớp nhầm với các câu hỏi chung chung không liên quan)
            words_ten = set(ten_chuan.split())
            words_cau = set(cau_hoi_chuan.split())
            tu_chung_co_nghia = {w for w in words_ten & words_cau if len(w) >= 3}
            score = len(tu_chung_co_nghia) * 15
            # 4. Dung sai lỗi chính tả / thiếu dấu: so khớp độ tương đồng chuỗi
            ratio = difflib.SequenceMatcher(None, ten_chuan, cau_hoi_chuan).ratio()
            if ratio > 0.55:
                score += int(ratio * 20)

        if score > best_score:
            best_score = score
            best_match = file

    if best_match and best_score >= 20:
        return load_thutuc_from_file(thutuc_folder, best_match), best_score

    return None, 0


def format_overview(thutuc: dict) -> str:
    """Trả lời tổng quan, chi tiết theo từng mục thay vì chỉ dán nguyên văn file"""
    sections = thutuc['sections']
    parts = [f"📌 THỦ TỤC: {thutuc['ten'].upper()}"]
    for key, emoji, label, _pattern in CATEGORY_META:
        content = sections.get(key)
        if content:
            parts.append(f"{emoji} {label}:\n{content}")

    if len(parts) == 1:
        # Không tách được mục nào -> trả nguyên văn nội dung gốc để không mất thông tin
        return thutuc['noi_dung_goc'].strip()

    parts.append('❓ Bạn cần biết thêm phần nào? Cứ hỏi tôi nhé, ví dụ: "hồ sơ cần gì", '
                  '"nộp ở đâu", "mất bao lâu", "có mất phí không"...')
    return '\n\n'.join(parts)


def format_field_answer(thutuc: dict, intent_key: str) -> str:
    """Trả lời đúng trọng tâm cho một câu hỏi cụ thể (VD: chỉ hỏi về lệ phí)"""
    label = CATEGORY_LABEL[intent_key]
    emoji = CATEGORY_EMOJI[intent_key]
    content = thutuc['sections'].get(intent_key)
    ten = thutuc['ten']

    if content:
        return (f"{emoji} {label} — {ten}:\n\n{content}\n\n"
                f"❓ Bạn cần hỏi thêm gì khác về thủ tục này không?")

    dia_chi = thutuc['sections'].get('dia_chi')
    lien_he = f"\n\n📍 Bạn có thể liên hệ trực tiếp tại: {dia_chi}" if dia_chi else ""
    return (f"Hiện dữ liệu về mục \"{label.lower()}\" của thủ tục \"{ten}\" chưa được cập nhật chi tiết."
            f"{lien_he}\n\nBạn có thể hỏi tôi về các phần khác như hồ sơ, đối tượng, địa chỉ nộp...")


def answer_question(thutuc_folder: Path, cau_hoi: str, last_procedure: str | None) -> ChatAnswer:
    """Trả lời một câu hỏi của người dùng.

    ``last_procedure`` là tên file thủ tục đang hỏi dở (đọc từ session ở tầng
    Flask, hoặc None nếu chưa có/đã hết hạn). Kết quả trả về mô tả nếu cần
    cập nhật ngữ cảnh đó qua ``context_action``/``context_value`` thay vì tự
    ghi vào session, để hàm này không phụ thuộc Flask.
    """
    cau_hoi = cau_hoi.strip()
    if not cau_hoi:
        return ChatAnswer('Xin lỗi, tôi không nghe rõ. Bạn vui lòng nói lại nhé! 🎤')
    cau_hoi_chuan = chuan_hoa_tim_kiem(cau_hoi)

    # ===== TỪ KHÓA ĐẶC BIỆT =====
    if co_tu_khoa(cau_hoi_chuan, ['chao', 'hi', 'hello']):
        return ChatAnswer(
            'Xin chào! Tôi có thể giúp gì cho bạn hôm nay? Hãy hỏi tôi về các thủ tục hành chính công nhé! 🎤',
            context_action='clear',
        )
    if co_tu_khoa(cau_hoi_chuan, ['cam on', 'thank']):
        return ChatAnswer('Dạ không có gì ạ! Rất vui được hỗ trợ bạn.')
    if co_tu_khoa(cau_hoi_chuan, ['tam biet', 'bye', 'thoat']):
        return ChatAnswer('Tạm biệt! Chúc bạn một ngày tốt lành! 👋', context_action='clear')

    # BHYT
    if co_tu_khoa(cau_hoi_chuan, ['bhyt', 'bao hiem y te', 'tra cuu bhyt']):
        return ChatAnswer("""🏥 **HƯỚNG DẪN TRA CỨU BHYT**

Bạn vui lòng truy cập trực tiếp vào trang web của Bảo hiểm xã hội Việt Nam theo đường dẫn dưới đây:

🔗 **Link tra cứu:** https://baohiemxahoi.gov.vn/tracuu/Pages/tra-cuu-thoi-han-su-dung-the-bhyt.aspx

📝 **Cách thực hiện:**
1. Nhập mã số thẻ BHYT vào ô tìm kiếm
2. Nhập mã xác nhận
3. Bấm "Tra cứu" để xem kết quả

💡 *Lưu ý: Trang web này do Bảo hiểm xã hội Việt Nam quản lý.*""")

    # Mẫu đơn (giữ từ khóa cụ thể để không nhầm với câu hỏi "cần giấy tờ gì" của một thủ tục)
    if co_tu_khoa(cau_hoi_chuan, ['mau don', 'bieu mau', 'mau giay', 'mau khai', 'to khai', 'kho mau']):
        return ChatAnswer("""📄 **KHO MẪU ĐƠN, TỜ KHAI**

Tôi đã chuẩn bị sẵn một kho lưu trữ các mẫu đơn, tờ khai hành chính cho bạn. Hãy bấm nút "Mẫu đơn, tờ khai" trên thanh công cụ để xem danh sách chi tiết.

💡 *Lưu ý: Bạn cần đăng nhập Google để xem và tải file.*""")

    # Khu phố
    if co_tu_khoa(cau_hoi_chuan, ['khu pho', 'thong tin khu pho', 'tra cuu khu pho']):
        return ChatAnswer("""🗺️ **THÔNG TIN KHU PHỐ**

Bạn có thể tra cứu thông tin chi tiết về các khu phố tại địa chỉ:

🔗 **Đường dẫn tra cứu:**
https://sites.google.com/view/phuongminhphung/trang-ch%E1%BB%A7

📝 **Cách thực hiện:**
1. Truy cập đường dẫn trên
2. Tìm kiếm thông tin về khu phố bạn quan tâm
3. Xem chi tiết thông tin hành chính

💡 *Trang thông tin này cung cấp dữ liệu chính thống về các khu phố của phường Minh Phụng.*""")

    # Danh sách thủ tục hiện có
    if co_tu_khoa(cau_hoi_chuan,
                  ['danh sach thu tuc', 'co nhung thu tuc gi', 'co nhung thu tuc nao',
                   'liet ke thu tuc', 'cac thu tuc hien co']):
        names = list_thutuc_names(thutuc_folder)
        if names:
            items = '\n'.join(f"• {ten}" for _f, ten in names)
            reply = (f"📑 Hiện tôi có thể hỗ trợ tra cứu các thủ tục sau:\n\n{items}\n\n"
                     f"❓ Bạn muốn hỏi về thủ tục nào? Hãy nhấn vào gợi ý bên dưới hoặc gõ/nói tên thủ tục nhé!")
            return ChatAnswer(reply, suggestions=[ten for _f, ten in names][:6])
        return ChatAnswer('Hiện tại tôi chưa có dữ liệu thủ tục nào. Vui lòng quay lại sau nhé!')

    # ===== TÌM THỦ TỤC + NHẬN DIỆN Ý ĐỊNH CÂU HỎI =====
    thutuc, _score = tim_kiem_thutuc(thutuc_folder, cau_hoi)
    intent = detect_intent(cau_hoi_chuan)

    if thutuc:
        reply = format_field_answer(thutuc, intent) if intent else format_overview(thutuc)
        return ChatAnswer(reply, context_action='set', context_value=thutuc['filename'])

    # Câu hỏi nối tiếp về thủ tục vừa hỏi trước đó (VD: "còn phí thì sao")
    if intent and last_procedure:
        thutuc_truoc = load_thutuc_from_file(thutuc_folder, last_procedure)
        if thutuc_truoc:
            return ChatAnswer(format_field_answer(thutuc_truoc, intent))

    # ===== KHÔNG TÌM THẤY =====
    goi_y = [ten for _f, ten in list_thutuc_names(thutuc_folder)][:4]
    reply = (f'❌ Rất tiếc, tôi chưa có thông tin về "{cau_hoi}".\n\n'
             f'💡 Bạn hãy thử hỏi tên chính xác của thủ tục, hoặc bấm nút chức năng '
             f'"Tra cứu BHYT", "Mẫu đơn", "Tra cứu Khu phố" nhé! 🎤')
    return ChatAnswer(reply, suggestions=goi_y, offer_ai=True)


def list_mau_don_folders(api_key: str | None, folder_id: str | None) -> tuple[list[dict], str | None]:
    """Liệt kê các thư mục con (mẫu đơn theo loại) trong thư mục Drive công khai.

    Trả về (folders, error): ``error`` là thông báo tiếng Việt để hiển thị
    khi chưa cấu hình hoặc khi gọi Google Drive API thất bại.
    """
    if not api_key or not folder_id:
        return [], "Chưa cấu hình kho mẫu đơn Google Drive trên máy chủ này."
    try:
        params = {
            'q': f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            'key': api_key,
            'fields': 'files(id, name)',
        }
        resp = requests.get(GOOGLE_DRIVE_API_URL, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get('files', []), None
    except requests.RequestException as error:
        return [], f"Không thể tải danh sách mẫu đơn: {error}"
