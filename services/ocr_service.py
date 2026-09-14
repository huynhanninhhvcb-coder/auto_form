"""Lớp OCR độc lập để có thể thay Tesseract bằng một nhà cung cấp khác sau này."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from collections import deque
from functools import lru_cache
from pathlib import Path
import re
import unicodedata

import pytesseract
from PIL import Image, ImageFilter, ImageOps
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


@lru_cache(maxsize=4)
def _cached_languages(tesseract_cmd: str | None) -> tuple[str, ...]:
    _configure_tesseract(tesseract_cmd)
    return tuple(_available_languages())


def detect_languages(tesseract_cmd: str | None = None) -> list[str]:
    """Cấu hình Tesseract và liệt kê gói ngôn ngữ đã cài, một lần duy nhất.

    ``tesseract --list-langs`` tốn 150 ms-1 s mỗi lần gọi (đo thực tế trên
    Windows). Nơi xử lý nhiều trang/tệp trong cùng một yêu cầu (``ocr_pdf``)
    nên gọi hàm này một lần rồi truyền kết quả cho mỗi lần OCR, thay vì để
    từng trang tự tra lại.
    """
    return list(_cached_languages(tesseract_cmd))


def _prepare_image(image: Image.Image, target_width: int = 1600) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    image = ImageOps.grayscale(image)
    image = ImageOps.autocontrast(image)
    # Đưa ảnh về một chiều rộng vừa đủ cho Tesseract (~1600px) thay vì luôn
    # nhân đôi ảnh nhỏ hoặc giữ nguyên ảnh chụp điện thoại độ phân giải cao
    # (3000-4000px): OCR ảnh lớn không cần thiết là phần tốn CPU nhất trên
    # máy chủ cấu hình thấp (vd. Render free 0.1 CPU).
    # Không phóng to ảnh nhỏ: nội suy không tạo thêm chi tiết chữ nhưng làm
    # Tesseract phải xử lý nhiều điểm ảnh hơn. Chỉ thu nhỏ ảnh chụp quá lớn.
    if image.width > target_width > 0:
        scale = target_width / image.width
        image = image.resize(
            (target_width, max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
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


def _ocr_cccd_mrz(
    image: Image.Image,
    languages: list[str],
    fallback_language: str,
    max_width: int = 2200,
) -> str:
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
        scale = min(2.0, max_width / crop.width)
        if abs(scale - 1.0) > 0.01:
            crop = crop.resize(
                (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
                Image.Resampling.LANCZOS,
            )

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


def ocr_pil_image(
    image: Image.Image,
    tesseract_cmd: str | None = None,
    languages: list[str] | None = None,
    max_workers: int = 5,
    target_width: int = 1600,
    preferred_language: str | None = None,
    fast_mode: bool = False,
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
    requested_languages = [part.strip() for part in (preferred_language or "").split("+") if part.strip()]
    if requested_languages and all(part in languages for part in requested_languages):
        language = "+".join(requested_languages)
    else:
        language = "vie+eng" if "vie" in languages and "eng" in languages else "vie" if "vie" in languages else "eng"
    if "vie" not in languages:
        warnings.append("Máy chủ chưa cài dữ liệu tiếng Việt cho Tesseract; kết quả OCR có thể sai dấu.")
    try:
        image = _detect_document_crop(image)
        prepared = _prepare_image(image, target_width=target_width)
        text = pytesseract.image_to_string(prepared, lang=language, config="--oem 3 --psm 6")
    except TesseractNotFoundError as error:
        raise OCRUnavailableError("Không thể khởi chạy Tesseract OCR.") from error
    except pytesseract.TesseractError as error:
        raise OCRUnavailableError(f"Tesseract không thể nhận dạng tệp: {error}") from error

    primary_text = text.strip()
    page_texts = [primary_text] if primary_text else []
    mrz_texts: list[str] = []
    if _looks_like_cccd(text, prepared):
        # Một ảnh đơn thường chỉ là mặt trước HOẶC mặt sau. Ở chế độ nhanh,
        # mặt trước chỉ cần OCR bố cục, mặt sau chỉ cần OCR MRZ; trước đây cả
        # hai lượt đều chạy cho mọi mặt thẻ dù một lượt chắc chắn vô ích.
        folded_text = _fold_for_detection(text)
        has_mrz_marker = bool(re.search(r"\b[1il]dvnm", folded_text))
        stacked_halves = _split_stacked_cccd(image)
        run_layout = not fast_mode or not has_mrz_marker or stacked_halves is not None
        run_mrz = not fast_mode or has_mrz_marker or stacked_halves is not None

        layout_image = prepared
        if stacked_halves is not None:
            front_region, back_region = stacked_halves
            layout_image = _prepare_image(front_region, target_width=target_width)
        else:
            back_region = image

        def _layout_call() -> str:
            # PSM 11 nhận các khối chữ rời của mặt trước tốt hơn PSM 6, trong
            # khi PSM 6 vẫn hữu ích cho bố cục thông thường. Giữ cả hai để
            # extractor chọn ứng viên có nhãn/độ tin cậy tốt hơn.
            try:
                layout_language = "vie" if "vie" in languages else language
                return pytesseract.image_to_string(
                    layout_image, lang=layout_language, config="--oem 3 --psm 11"
                ).strip()
            except pytesseract.TesseractError:
                return ""

        # Ảnh ghép dọc mặt trước/sau (một tệp chứa cả hai mặt) có tỉ lệ khung
        # ảnh của cả composite, không phải của một mặt thẻ nằm ngang. Tách đôi
        # trước khi đọc MRZ, nếu không toạ độ tương đối của vùng MRZ (52%-97%
        # chiều cao ảnh) sẽ trật khỏi mặt sau.
        # CPU máy chủ triển khai (vd. Render free 0.1 CPU) quá yếu để chạy nổi
        # các lượt OCR vùng chuyên biệt (tên/ngày sinh/giới tính/địa chỉ) từng
        # có ở đây: mỗi lượt là một tiến trình Tesseract riêng, tốn vài giây
        # bất kể ảnh to hay nhỏ, và một ảnh CCCD có thể kéo theo hơn chục lượt
        # như vậy. Giờ chỉ giữ lượt đọc bố cục (bổ sung cho lượt đọc chính ở
        # trên) và MRZ. Đổi lại, một số CCCD chụp lóa sáng/mờ/kiểu cũ có thể
        # thiếu tên, ngày sinh hoặc địa chỉ và cần người dùng tự nhập bù.
        task_count = int(run_layout) + int(run_mrz)
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, task_count))) as executor:
            layout_future = executor.submit(_layout_call) if run_layout else None
            mrz_future = (
                executor.submit(
                    _ocr_cccd_mrz,
                    back_region,
                    languages,
                    language,
                    1400 if fast_mode else 2200,
                )
                if run_mrz
                else None
            )

            if layout_future is not None:
                layout_text = layout_future.result()
                if layout_text and layout_text not in primary_text:
                    page_texts.append(f"--- CCCD BỐ CỤC ---\n{layout_text}")

            if mrz_future is not None:
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
    target_width: int = 1600,
    preferred_language: str | None = None,
    fast_mode: bool = False,
) -> OCRResult:
    try:
        if languages is None:
            languages = detect_languages(tesseract_cmd)
        with Image.open(image_path) as image:
            return ocr_pil_image(
                image,
                tesseract_cmd=tesseract_cmd,
                languages=languages,
                max_workers=max_workers,
                target_width=target_width,
                preferred_language=preferred_language,
                fast_mode=fast_mode,
            )
    except (OSError, ValueError) as error:
        raise ValueError("Tệp ảnh không hợp lệ hoặc bị hỏng.") from error
