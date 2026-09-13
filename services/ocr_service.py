"""Lớp OCR độc lập để có thể thay Tesseract bằng một nhà cung cấp khác sau này."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from collections import deque
from pathlib import Path
import re
import unicodedata

import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from pytesseract import TesseractNotFoundError


class OCRUnavailableError(RuntimeError):
    """Tesseract hoặc dữ liệu ngôn ngữ cần thiết chưa sẵn sàng."""


@dataclass
class OCRResult:
    text: str
    warnings: list[str] = field(default_factory=list)
    source: str = "ocr"
    # PDF cần giữ ranh giới trang để lớp trích xuất ưu tiên đúng tài liệu nguồn.
    pages: list[str] = field(default_factory=list)
    # Vùng MRZ mặt sau CCCD được OCR riêng bằng whitelist Latin/chữ số.
    # Không ghép vào raw text để lớp trích xuất còn kiểm tra checksum riêng.
    mrz_texts: list[str] = field(default_factory=list)


def _configure_tesseract(tesseract_cmd: str | None) -> None:
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd


def _available_languages() -> list[str]:
    try:
        return pytesseract.get_languages(config="")
    except TesseractNotFoundError as error:
        raise OCRUnavailableError(
            "Chưa tìm thấy Tesseract. Cài Tesseract OCR và đặt TESSERACT_CMD nếu nó không nằm trong PATH."
        ) from error


def detect_languages(tesseract_cmd: str | None = None) -> list[str]:
    """Cấu hình Tesseract và liệt kê gói ngôn ngữ đã cài, một lần duy nhất.

    ``tesseract --list-langs`` tốn 150 ms-1 s mỗi lần gọi (đo thực tế trên
    Windows). Nơi xử lý nhiều trang/tệp trong cùng một yêu cầu (``ocr_pdf``)
    nên gọi hàm này một lần rồi truyền kết quả cho mỗi lần OCR, thay vì để
    từng trang tự tra lại.
    """
    _configure_tesseract(tesseract_cmd)
    return _available_languages()


def _prepare_image(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    image = ImageOps.grayscale(image)
    image = ImageOps.autocontrast(image)
    # Đưa ảnh về một chiều rộng vừa đủ cho Tesseract (~1600px) thay vì luôn
    # nhân đôi ảnh nhỏ hoặc giữ nguyên ảnh chụp điện thoại độ phân giải cao
    # (3000-4000px): OCR ảnh lớn không cần thiết là phần tốn CPU nhất trên
    # máy chủ cấu hình thấp (vd. Render free 0.1 CPU).
    target_width = 1600
    if image.width > 0 and image.width != target_width:
        scale = min(1.4, target_width / image.width)
        if scale < 0.995 or scale > 1.005:
            image = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            )
    return image


def _otsu_threshold(histogram: list[int], total: int) -> int:
    """Return an automatic foreground threshold for an 8-bit image."""
    weighted_sum = sum(level * count for level, count in enumerate(histogram))
    background_weight = 0
    background_sum = 0
    best_variance = -1.0
    best_level = 127
    for level, count in enumerate(histogram):
        background_weight += count
        if not background_weight:
            continue
        foreground_weight = total - background_weight
        if not foreground_weight:
            break
        background_sum += level * count
        background_mean = background_sum / background_weight
        foreground_mean = (weighted_sum - background_sum) / foreground_weight
        variance = background_weight * foreground_weight * (background_mean - foreground_mean) ** 2
        if variance > best_variance:
            best_variance = variance
            best_level = level
    return best_level


def _detect_document_crop(image: Image.Image) -> Image.Image:
    """Crop a photographed card/document surrounded by a large background.

    Phone photos often contain much more table/wall than the document itself.
    Tesseract then treats the textured background as characters.  Work on a
    small blurred grayscale copy, find the largest coherent bright rectangle,
    and map its bounds back to the original image.  Conservative acceptance
    rules keep ordinary scans and full-frame cards unchanged.
    """
    source = ImageOps.exif_transpose(image)
    if source.width < 500 or source.height < 500:
        return source

    scale = min(1.0, 360 / max(source.width, source.height))
    sample = source.resize(
        (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
        Image.Resampling.BILINEAR,
    )
    gray = ImageOps.grayscale(sample).filter(ImageFilter.GaussianBlur(radius=2.2))
    threshold = _otsu_threshold(gray.histogram(), gray.width * gray.height)
    pixels = gray.load()
    width, height = gray.size
    # Old Vietnamese driving licences have a warm cream background.  Its
    # colour separates reliably from a grey/green table even when luminance
    # alone considers the upper half of the table foreground.
    colour_sample = sample.convert("RGB").filter(ImageFilter.GaussianBlur(radius=2.2))
    colour_pixels = colour_sample.load()
    warm = bytearray(
        1
        if colour_pixels[x, y][0] - colour_pixels[x, y][2] > 22 and pixels[x, y] > 105
        else 0
        for y in range(height)
        for x in range(width)
    )
    warm_ratio = sum(warm) / (width * height)
    bright = warm if 0.04 <= warm_ratio <= 0.65 else bytearray(
        1 if pixels[x, y] > threshold else 0 for y in range(height) for x in range(width)
    )
    visited = bytearray(width * height)
    best: tuple[int, int, int, int, int] | None = None

    for start in range(width * height):
        if not bright[start] or visited[start]:
            continue
        queue = deque([start])
        visited[start] = 1
        count = 0
        min_x = max_x = start % width
        min_y = max_y = start // width
        while queue:
            index = queue.popleft()
            x, y = index % width, index // width
            count += 1
            min_x, max_x = min(min_x, x), max(max_x, x)
            min_y, max_y = min(min_y, y), max(max_y, y)
            for neighbour in (index - 1, index + 1, index - width, index + width):
                if neighbour < 0 or neighbour >= width * height or visited[neighbour] or not bright[neighbour]:
                    continue
                nx, ny = neighbour % width, neighbour // width
                if abs(nx - x) + abs(ny - y) != 1:
                    continue
                visited[neighbour] = 1
                queue.append(neighbour)
        bounds_area = (max_x - min_x + 1) * (max_y - min_y + 1)
        fill_ratio = count / bounds_area
        candidate_width = max_x - min_x + 1
        candidate_height = max_y - min_y + 1
        candidate_aspect = candidate_width / max(1, candidate_height)
        candidate_area_ratio = bounds_area / (width * height)
        candidate = (count, min_x, min_y, max_x + 1, max_y + 1)
        if (
            1.15 <= candidate_aspect <= 2.2
            and 0.07 <= candidate_area_ratio <= 0.88
            and fill_ratio >= 0.34
            and (best is None or count > best[0])
        ):
            best = candidate

    if best is None:
        return source
    _, left, top, right, bottom = best
    crop_width, crop_height = right - left, bottom - top
    aspect = crop_width / max(1, crop_height)
    area_ratio = crop_width * crop_height / (width * height)
    width_ratio = crop_width / width
    if not 1.15 <= aspect <= 2.2 or not 0.07 <= area_ratio <= 0.88 or width_ratio < 0.65:
        return source

    margin = max(2, round(min(crop_width, crop_height) * 0.025))
    left, top = max(0, left - margin), max(0, top - margin)
    right, bottom = min(width, right + margin), min(height, bottom + margin)
    return source.crop(
        (
            round(left / scale),
            round(top / scale),
            round(right / scale),
            round(bottom / scale),
        )
    )


def _fold_for_detection(text: str) -> str:
    """Bỏ dấu và ký tự nhiễu nhỏ để nhận biết nhanh bố cục CCCD."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(character for character in decomposed if unicodedata.category(character) != "Mn").replace("đ", "d").replace("Đ", "D").lower()


def _looks_like_cccd(text: str, image: Image.Image) -> bool:
    """Recognise a single horizontal CCCD or a vertical front/back composite."""
    if image.height == 0:
        return False
    ratio = image.width / image.height
    folded = _fold_for_detection(text)
    has_mrz_marker = bool(re.search(r"\b[1il]dvnm", folded))
    identity_markers = bool(
        re.search(r"can\s*cuo\W*c", folded)
        or "citizen identity" in folded
        or has_mrz_marker
    )
    horizontal_card = 1.35 <= ratio <= 2.15
    stacked_sides = 0.65 <= ratio <= 0.95 and has_mrz_marker and identity_markers
    return identity_markers and (horizontal_card or stacked_sides)


def _looks_like_cccd_front(text: str) -> bool:
    folded = _fold_for_detection(text)
    return bool(re.search(r"can\s*cuo\W*c", folded) or "citizen identity" in folded)


def _split_stacked_cccd(image: Image.Image) -> tuple[Image.Image, Image.Image] | None:
    """Tách ảnh ghép dọc mặt trước/sau CCCD thành hai nửa dạng một mặt thẻ.

    Người dân thường chụp/ghép mặt trước và mặt sau CCCD vào chung một tệp
    ảnh (mặt trước ở trên, mặt sau ở dưới). Các hàm đọc theo vùng cố định bên
    dưới tính tọa độ theo tỉ lệ của MỘT mặt thẻ nằm ngang; áp thẳng lên toàn
    bộ ảnh ghép sẽ trật hoàn toàn, ví dụ vùng MRZ vốn lấy 52%-97% chiều cao
    ảnh sẽ rơi vào giữa hai mặt thay vì nửa dưới của mặt sau. Cắt đôi ảnh
    (chờm nhẹ để không hụt mép) rồi trả về (mặt trước, mặt sau) để các hàm đó
    chạy lại đúng như trên một ảnh một mặt thẻ độc lập.
    """
    if image.height == 0:
        return None
    ratio = image.width / image.height
    if not 0.6 <= ratio <= 1.0:
        return None
    overlap = round(image.height * 0.03)
    midpoint = image.height // 2
    top = image.crop((0, 0, image.width, min(image.height, midpoint + overlap)))
    bottom = image.crop((0, max(0, midpoint - overlap), image.width, image.height))
    return top, bottom


def _ocr_cccd_mrz(image: Image.Image, languages: list[str], fallback_language: str) -> str:
    """Đọc riêng ba dòng MRZ ở nửa dưới CCCD bằng bộ ký tự hạn chế.

    Cách cắt theo tỷ lệ giữ ứng dụng không phụ thuộc OpenCV và hoạt động với
    ảnh chụp ngang phổ biến. Dữ liệu trả về vẫn phải vượt checksum ở tầng
    trích xuất trước khi được điền vào biểu mẫu.
    """
    image = ImageOps.exif_transpose(image)
    left, top = int(image.width * 0.04), int(image.height * 0.52)
    right, bottom = int(image.width * 0.96), int(image.height * 0.97)
    if right <= left or bottom <= top:
        return ""

    crop = ImageOps.autocontrast(ImageOps.grayscale(image.crop((left, top, right, bottom))))
    if crop.width:
        scale = min(2.0, 2200 / crop.width)
        if scale > 1:
            crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.Resampling.LANCZOS)

    language = "eng" if "eng" in languages else fallback_language
    try:
        text = pytesseract.image_to_string(
            crop,
            lang=language,
            config="--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<",
        )
    except pytesseract.TesseractError:
        return ""

    lines = [re.sub(r"[^A-Z0-9<]", "", line.upper()) for line in text.splitlines()]
    lines = ["IDVNM" + line[5:] if re.match(r"^[1L]DVNM", line) else line for line in lines if line]
    if any(line.startswith("IDVNM") for line in lines) and any("<" in line for line in lines):
        return "\n".join(lines)
    return ""


def _ocr_cccd_front_address_line(
    image: Image.Image,
    bounds: tuple[float, float, float, float],
    language: str,
) -> str:
    """Đọc một dòng địa chỉ theo vùng cố định của bố cục CCCD mặt trước."""
    left_ratio, top_ratio, right_ratio, bottom_ratio = bounds
    left, top = int(image.width * left_ratio), int(image.height * top_ratio)
    right, bottom = int(image.width * right_ratio), int(image.height * bottom_ratio)
    if right <= left or bottom <= top:
        return ""

    crop = ImageOps.autocontrast(ImageOps.grayscale(image.crop((left, top, right, bottom))))
    if crop.width:
        scale = min(2.0, 1800 / crop.width)
        if scale > 1:
            crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.Resampling.LANCZOS)
    try:
        text = pytesseract.image_to_string(crop, lang=language, config="--oem 3 --psm 7")
    except pytesseract.TesseractError:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"^[^0-9A-Za-zÀ-ỹĐđ]+|[^0-9A-Za-zÀ-ỹĐđ.,/\- ]+$", "", text).strip()


def _is_cccd_address_line(value: str, *, administrative_line: bool = False) -> bool:
    """Loại kết quả OCR nhiễu trước khi dùng crop dòng địa chỉ riêng."""
    folded = _fold_for_detection(value)
    letters = re.sub(r"[^a-z]", "", folded)
    if not 5 <= len(value) <= 150 or len(letters) < 4:
        return False
    if not administrative_line:
        return True
    return bool(re.search(r"\b(?:phuong|xa|thi\s*tran|quan|huyen|tinh|thanh\s*pho|tp)\b", folded))


# Hai dòng địa chỉ thường trú in ổn định ở nửa phải dưới của CCCD gắn chip.
# ``_ocr_cccd_front_address`` và ``_ocr_cccd_front_address_lines`` từng tự
# OCR lại đúng hai vùng này một cách độc lập (toạ độ lệch nhau chưa tới
# 0.002, tức cùng một chỗ), tốn gấp đôi số lượt gọi Tesseract một cách vô
# ích. Dùng chung một cặp toạ độ để hai bên có thể chia sẻ cùng một kết quả
# OCR mà vẫn tự áp quy tắc xác thực/gắn nhãn riêng của mình.
_CCCD_ADDRESS_LINE_BOXES: tuple[tuple[float, float, float, float], tuple[float, float, float, float]] = (
    (0.64, 0.776, 0.95, 0.868),
    (0.336, 0.837, 0.952, 0.920),
)


def _ocr_cccd_front_address_line_pair(image: Image.Image, language: str) -> tuple[str, str]:
    """OCR hai dòng địa chỉ thường trú một lần duy nhất, dùng chung cho các bên gọi."""
    image = ImageOps.exif_transpose(image)
    first_line = _ocr_cccd_front_address_line(image, _CCCD_ADDRESS_LINE_BOXES[0], language)
    second_line = _ocr_cccd_front_address_line(image, _CCCD_ADDRESS_LINE_BOXES[1], language)
    return first_line, second_line


def _ocr_cccd_front_address(
    image: Image.Image,
    languages: list[str],
    fallback_language: str,
    line_pair: tuple[str, str] | None = None,
) -> str:
    """Đọc vùng thường trú ở góc phải dưới mặt trước CCCD.

    ``vie+eng`` thường làm rơi dấu ở hai dòng địa chỉ vì bộ nhận dạng tiếng
    Anh chi phối các tên riêng. Khi có dữ liệu ``vie``, đọc từng dòng bằng PSM
    7 trước; vùng rộng PSM 6 bên dưới là phương án dự phòng cho ảnh có bố cục
    lệch hoặc CCCD cũ. ``line_pair`` cho phép nơi gọi (``ocr_pil_image``)
    truyền kết quả OCR hai dòng đã đọc sẵn thay vì đọc lại.
    """
    image = ImageOps.exif_transpose(image)
    language = "vie" if "vie" in languages else fallback_language

    if "vie" in languages:
        first_line, second_line = line_pair if line_pair is not None else _ocr_cccd_front_address_line_pair(
            image, language
        )
        if _is_cccd_address_line(first_line) and _is_cccd_address_line(second_line, administrative_line=True):
            return f"Nơi thường trú: {first_line}\n{second_line}"

    left, top = int(image.width * 0.32), int(image.height * 0.68)
    right, bottom = int(image.width * 0.98), int(image.height * 0.95)
    if right <= left or bottom <= top:
        return ""

    crop = ImageOps.autocontrast(ImageOps.grayscale(image.crop((left, top, right, bottom))))
    crop = ImageEnhance.Contrast(crop).enhance(1.6)
    crop = crop.filter(ImageFilter.UnsharpMask(radius=1.5, percent=120, threshold=3))
    if crop.width:
        scale = min(2.0, 2200 / crop.width)
        if scale > 1:
            crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.Resampling.LANCZOS)

    try:
        text = pytesseract.image_to_string(crop, lang=language, config="--oem 3 --psm 6").strip()
    except pytesseract.TesseractError:
        return ""
    folded = _fold_for_detection(text)
    if "noi thuong tru" in folded or "place of residence" in folded:
        return text
    return ""


def _clean_cccd_address_line(value: str) -> str:
    """Remove OCR punctuation around a single CCCD address line.

    The OCR is deliberately not used to "correct" Vietnamese words here: any
    spelling correction without a trusted source could turn a real address
    into a different one.  This only discards isolated quotation/dash marks
    that Tesseract commonly emits at the borders of a tightly cropped line.
    """
    value = " ".join(part.strip() for part in value.splitlines() if part.strip())
    value = re.sub(r"^[^0-9A-Za-zÀ-ỹĐđ]+", "", value)
    value = value.strip(" \t|`'\"")
    return re.sub(r"(?:\s*[.\-–—])+\s*$", "", value).strip()


def _ocr_cccd_front_address_lines(
    image: Image.Image,
    languages: list[str],
    line_pair: tuple[str, str] | None = None,
) -> str:
    """OCR the two printed residence-address lines on a CCCD front side.

    Tesseract's combined ``vie+eng`` model favours the English subtitle on a
    CCCD and can drop Vietnamese diacritics from the address.  On the standard
    horizontal front-card layout, narrowly cropped address lines with the
    Vietnamese model and single-line segmentation preserve them substantially
    better.  The result is accepted only when both expected address shapes are
    present; otherwise the regular, wider OCR crop remains the fallback.
    ``line_pair`` cho phép nơi gọi truyền kết quả OCR hai dòng đã đọc sẵn
    (xem ``_ocr_cccd_front_address``) thay vì đọc lại đúng vùng ảnh đó.
    """
    if "vie" not in languages:
        return ""

    image = ImageOps.exif_transpose(image)
    if image.width < 300 or image.height < 180:
        return ""

    if line_pair is not None:
        raw_first, raw_second = line_pair
    else:
        raw_first, raw_second = _ocr_cccd_front_address_line_pair(image, "vie")
    first_line, second_line = _clean_cccd_address_line(raw_first), _clean_cccd_address_line(raw_second)
    first_match = re.search(r"(?<!\d)(\d{1,5}\s*/\s*\d{1,5}\b.*)", first_line)
    if not first_match:
        return ""
    first_line = _clean_cccd_address_line(first_match.group(1))
    second_folded = _fold_for_detection(second_line)
    if len(first_line) < 7 or len(second_line) < 8 or not re.search(
        r"\b(?:phuong|quan|tp|thanh\s*pho|xa|huyen|tinh)\b", second_folded
    ):
        return ""

    # Include a stable label so the extraction layer can use its normal,
    # label-based address parser.  It is still marked for human review there.
    return f"Nơi thường trú / Place of residence:\n{first_line}\n{second_line}"


def _ocr_cccd_front_details(image: Image.Image, languages: list[str], fallback_language: str) -> str:
    """Read critical fields from fixed regions when whole-card OCR is degraded.

    Glare, security patterns and a portrait cause Tesseract's page segmentation
    to omit otherwise legible values.  Region OCR is only enabled after the
    image has already been identified as a CCCD front side.
    """
    image = ImageOps.exif_transpose(image)
    if image.width < 700 or image.height < 400:
        return ""
    language = "vie" if "vie" in languages else fallback_language

    def read_region(
        bounds: tuple[float, float, float, float],
        *,
        psm: int,
        lang: str = language,
        whitelist: str = "",
    ) -> str:
        left, top, right, bottom = (
            int(image.width * bounds[0]),
            int(image.height * bounds[1]),
            int(image.width * bounds[2]),
            int(image.height * bounds[3]),
        )
        crop = ImageOps.autocontrast(ImageOps.grayscale(image.crop((left, top, right, bottom))))
        crop = crop.resize((round(crop.width * 1.5), round(crop.height * 1.5)), Image.Resampling.LANCZOS)
        config = f"--oem 3 --psm {psm}"
        if whitelist:
            config += f" -c tessedit_char_whitelist={whitelist}"
        try:
            return pytesseract.image_to_string(crop, lang=lang, config=config).strip()
        except pytesseract.TesseractError:
            return ""

    details: list[str] = []

    name_text = read_region((0.27, 0.45, 0.78, 0.62), psm=11)
    if name_text:
        # Keep the label and value together; the extraction layer still
        # validates that the next line has the shape of a Vietnamese name.
        details.append(name_text)

    numeric_language = "eng" if "eng" in languages else language
    date_text = read_region(
        (0.56, 0.58, 0.80, 0.68),
        psm=7,
        lang=numeric_language,
        whitelist="0123456789/.-",
    )
    date_match = re.search(r"(?<!\d)(\d{1,2}[/.-]\d{1,2}[/.-](?:19|20)\d{2})(?!\d)", date_text)
    if date_match:
        details.append(f"Ngày sinh: {date_match.group(1)}")

    gender_text = read_region((0.45, 0.62, 0.72, 0.76), psm=6)
    gender_folded = _fold_for_detection(gender_text)
    gender_match = re.search(r"\b(nam|nu)\b", gender_folded)
    if gender_match:
        details.append("Giới tính: Nam" if gender_match.group(1) == "nam" else "Giới tính: Nữ")

    address_bounds = (0.27, 0.70, 0.98, 0.99)
    address_layout = read_region(address_bounds, psm=11, lang="vie+eng" if "eng" in languages and "vie" in languages else language)
    address_block = read_region(address_bounds, psm=6)
    first_line = ""
    for line in address_layout.splitlines():
        match = re.search(r"(?<!\d)(\d{1,5}\s*/\s*\d{1,5}\b[^\n]{2,80})", line)
        if match:
            first_line = _clean_cccd_address_line(match.group(1))
            first_line = re.sub(r"(?:\s*[-–—])?\s+[A-Za-zÀ-ỹĐđ]$", "", first_line).rstrip(" -–—")
            break
    second_line = ""
    for line in address_block.splitlines():
        clean_line = _clean_cccd_address_line(line)
        folded_line = _fold_for_detection(clean_line)
        if re.search(r"\b(?:phuong|quan|tp|thanh\s*pho|p\.?\s*0?\d)\b", folded_line):
            second_line = clean_line
    if first_line and second_line:
        details.append(f"Nơi thường trú / Place of residence:\n{first_line}\n{second_line}")

    return "\n".join(details)


def ocr_pil_image(
    image: Image.Image,
    tesseract_cmd: str | None = None,
    languages: list[str] | None = None,
    max_workers: int = 5,
) -> OCRResult:
    """OCR một ảnh đã mở.

    ``languages`` cho phép nơi gọi (vd. OCR nhiều trang PDF) tra cứu gói ngôn
    ngữ một lần rồi truyền vào, thay vì để mỗi lần gọi tự tốn thêm một lượt
    gọi tiến trình ``tesseract --list-langs``.
    """
    if languages is None:
        _configure_tesseract(tesseract_cmd)
        languages = _available_languages()
    if not languages:
        raise OCRUnavailableError("Tesseract không có gói ngôn ngữ nào để nhận dạng văn bản.")

    warnings: list[str] = []
    language = "vie+eng" if "vie" in languages and "eng" in languages else "vie" if "vie" in languages else "eng"
    if "vie" not in languages:
        warnings.append("Máy chủ chưa cài dữ liệu tiếng Việt cho Tesseract; kết quả OCR có thể sai dấu.")
    try:
        image = _detect_document_crop(image)
        prepared = _prepare_image(image)
        text = pytesseract.image_to_string(prepared, lang=language, config="--oem 3 --psm 6")
    except TesseractNotFoundError as error:
        raise OCRUnavailableError("Không thể khởi chạy Tesseract OCR.") from error
    except pytesseract.TesseractError as error:
        raise OCRUnavailableError(f"Tesseract không thể nhận dạng tệp: {error}") from error

    primary_text = text.strip()
    page_texts = [primary_text] if primary_text else []
    mrz_texts: list[str] = []
    if _looks_like_cccd(text, prepared):
        def _layout_call() -> str:
            # PSM 11 nhận các khối chữ rời của mặt trước tốt hơn PSM 6, trong
            # khi PSM 6 vẫn hữu ích cho bố cục thông thường. Giữ cả hai để
            # extractor chọn ứng viên có nhãn/độ tin cậy tốt hơn.
            try:
                layout_language = "vie" if "vie" in languages else language
                return pytesseract.image_to_string(
                    prepared, lang=layout_language, config="--oem 3 --psm 11"
                ).strip()
            except pytesseract.TesseractError:
                return ""

        # Ảnh ghép dọc mặt trước/sau (một tệp chứa cả hai mặt) có tỉ lệ khung
        # ảnh của cả composite, không phải của một mặt thẻ nằm ngang. Tách đôi
        # trước khi áp các hàm đọc theo vùng, nếu không toàn bộ tọa độ tương
        # đối bên dưới sẽ trật, đặc biệt là vùng MRZ và địa chỉ thường trú.
        front_region, back_region = image, image
        stacked_halves = _split_stacked_cccd(image)
        if stacked_halves is not None:
            front_region, back_region = stacked_halves

        is_horizontal_card = front_region.height > 0 and 1.30 <= front_region.width / front_region.height <= 2.25
        run_front_details = (stacked_halves is not None or _looks_like_cccd_front(text)) and is_horizontal_card

        # Mỗi vùng dưới đây là một tiến trình `tesseract` riêng (đo thực tế
        # 0.4-1.3 giây/lần trên Windows) và không phụ thuộc lẫn nhau, nên chạy
        # song song thay vì tuần tự giúp giảm đáng kể thời gian OCR một ảnh
        # CCCD. Kết quả vẫn được xử lý và thêm vào page_texts theo đúng thứ tự
        # cũ để không đổi hành vi so sánh điểm ở tầng trích xuất.
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
            layout_future = executor.submit(_layout_call)
            detail_future = (
                executor.submit(_ocr_cccd_front_details, front_region, languages, language)
                if run_front_details
                else None
            )
            # `_ocr_cccd_front_address_lines` và `_ocr_cccd_front_address` xác
            # thực/gắn nhãn địa chỉ theo hai quy tắc khác nhau nhưng đọc gần
            # như đúng cùng một cặp vùng ảnh; OCR cặp dòng đó một lần rồi
            # truyền cho cả hai thay vì mỗi hàm tự đọc lại (xem
            # `_CCCD_ADDRESS_LINE_BOXES`).
            address_line_pair_future = (
                executor.submit(_ocr_cccd_front_address_line_pair, front_region, "vie")
                if run_front_details and "vie" in languages
                else None
            )
            mrz_future = executor.submit(_ocr_cccd_mrz, back_region, languages, language)

            layout_text = layout_future.result()
            if layout_text and layout_text not in primary_text:
                page_texts.append(f"--- CCCD BỐ CỤC ---\n{layout_text}")

            if run_front_details:
                detail_text = detail_future.result()
                if detail_text:
                    page_texts.append(f"--- CCCD CHI TIẾT ---\n{detail_text}")
                address_line_pair = address_line_pair_future.result() if address_line_pair_future else None
                detailed_address_text = _ocr_cccd_front_address_lines(
                    front_region, languages, line_pair=address_line_pair
                )
                if detailed_address_text:
                    # Keep this before the wider crop below.  Both receive the
                    # same review status/priority; preserving order lets the
                    # diacritic-friendly result win a score tie in the extractor.
                    page_texts.append(f"--- CCCD ĐỊA CHỈ RÕ ---\n{detailed_address_text}")
                address_text = _ocr_cccd_front_address(
                    front_region, languages, language, line_pair=address_line_pair
                )
                if address_text:
                    page_texts.append(f"--- CCCD ĐỊA CHỈ ---\n{address_text}")

            mrz_text = mrz_future.result()
            if mrz_text:
                mrz_texts.append(mrz_text)

    cleaned_text = "\n\n".join(page_texts).strip()
    return OCRResult(
        text=cleaned_text,
        warnings=warnings,
        pages=page_texts,
        mrz_texts=mrz_texts,
    )


def ocr_image(
    image_path: str | Path,
    tesseract_cmd: str | None = None,
    languages: list[str] | None = None,
    max_workers: int = 5,
) -> OCRResult:
    try:
        with Image.open(image_path) as image:
            return ocr_pil_image(image, tesseract_cmd=tesseract_cmd, languages=languages, max_workers=max_workers)
    except (OSError, ValueError) as error:
        raise ValueError("Tệp ảnh không hợp lệ hoặc bị hỏng.") from error
