"""Tạo mẫu hỏa táng A4 bám theo biểu mẫu chính thức hai trang."""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "templates_word" / "ho_tro_hoa_tang.docx"


def _font(run, *, size: float = 12, bold: bool = False, italic: bool = False) -> None:
    run.font.name = "Times New Roman"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic


def _paragraph(document, *, after: float = 0, line: float = 1.0):
    paragraph = document.add_paragraph()
    fmt = paragraph.paragraph_format
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(after)
    fmt.line_spacing_rule = WD_LINE_SPACING.SINGLE
    fmt.line_spacing = line
    return paragraph


def _center(document, text: str, *, size: float = 12, bold: bool = False, after: float = 0):
    paragraph = _paragraph(document, after=after)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _font(paragraph.add_run(text), size=size, bold=bold)
    return paragraph


def _remove_table_borders(table) -> None:
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = OxmlElement(f"w:{edge}")
        node.set(qn("w:val"), "nil")
        borders.append(node)
    table._tbl.tblPr.append(borders)


def _field(document, prefix: str, token: str, *, after: float = 2, indent: float = 0) -> None:
    paragraph = _paragraph(document, after=after)
    paragraph.paragraph_format.left_indent = Cm(indent)
    _font(paragraph.add_run(prefix))
    _font(paragraph.add_run(f"{{{{{token}}}}}"), bold=True)


def _option(document, text: str, token: str, *, level: int = 0, after: float = 0) -> None:
    paragraph = _paragraph(document, after=after)
    paragraph.paragraph_format.left_indent = Cm(1.35 + 0.35 * level)
    paragraph.paragraph_format.first_line_indent = Cm(-0.35)
    paragraph.paragraph_format.tab_stops.add_tab_stop(Cm(15.75), WD_TAB_ALIGNMENT.RIGHT)
    marker = "• " if level else "- "
    _font(paragraph.add_run(marker + text))
    _font(paragraph.add_run(f"\t{{{{{token}}}}}"), size=12)


def _configure_section(section) -> None:
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(1.65)
    section.bottom_margin = Cm(1.35)
    section.left_margin = Cm(2.25)
    section.right_margin = Cm(2.0)
    section.header_distance = Cm(0.5)
    section.footer_distance = Cm(0.5)


def build_template() -> Path:
    document = Document()
    _configure_section(document.sections[0])
    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Times New Roman")
    normal.font.size = Pt(12)

    _center(document, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", bold=True)
    motto = _center(document, "Độc lập - Tự do - Hạnh phúc", bold=True, after=8)
    motto.runs[0].underline = True
    _center(document, "TỜ KHAI THÔNG TIN GIA ĐÌNH, CÁ NHÂN, TỔ CHỨC", size=13, bold=True)
    _center(document, "NHẬN CHI PHÍ HỖ TRỢ KHUYẾN KHÍCH HỎA TÁNG", size=13, bold=True, after=7)
    _center(document, "Kính gửi: Ủy ban nhân dân Phường Minh Phụng, TP. Hồ Chí Minh", after=8)

    _field(document, "1. Tôi tên là: ", "full_name")
    identity = _paragraph(document, after=2)
    identity.paragraph_format.tab_stops.add_tab_stop(Cm(9.0))
    _font(identity.add_run("2. Ngày, tháng, năm sinh: "))
    _font(identity.add_run("{{date_of_birth}}"), bold=True)
    _font(identity.add_run("\t3. CCCD số: "))
    _font(identity.add_run("{{citizen_id}}"), bold=True)
    _field(document, "4. Hộ khẩu thường trú: ", "residence_address")

    bank = _paragraph(document, after=2)
    bank.paragraph_format.left_indent = Cm(0.5)
    bank.paragraph_format.tab_stops.add_tab_stop(Cm(9.2))
    _font(bank.add_run("Số Tài khoản: "))
    _font(bank.add_run("{{bank_account_number}}"), bold=True)
    _font(bank.add_run("\tNgân hàng: "))
    _font(bank.add_run("{{bank_name}}"), bold=True)
    _field(document, "Số điện thoại: ", "phone_number", indent=0.5)
    _field(document, "5. Quan hệ với người mất: ", "deceased_relationship")
    _field(document, "Hoặc đại diện cho tổ chức (nếu có): ", "org_name", indent=0.5)
    _field(document, "6. Họ và tên người mất: ", "deceased_full_name")
    _field(document, "7. Đã từ trần ngày: ", "deceased_death_date")

    certificate = _paragraph(document, after=2)
    certificate.paragraph_format.left_indent = Cm(0.55)
    _font(certificate.add_run("(Giấy chứng tử số: "))
    _font(certificate.add_run("{{death_certificate_number}}"), bold=True)
    _font(certificate.add_run(" ngày "))
    _font(certificate.add_run("{{death_certificate_date}}"), bold=True)
    _font(certificate.add_run(" do "))
    _font(certificate.add_run("{{death_certificate_issuer}}"), bold=True)
    _font(certificate.add_run(" cấp)"))

    heading = _paragraph(document, after=1)
    heading.paragraph_format.left_indent = Cm(1.0)
    _font(heading.add_run("8. Thuộc đối tượng:"))
    options_page_1 = (
        ("Bà mẹ Việt Nam anh hùng", 1, 0),
        ("Anh hùng lực lượng vũ trang nhân dân, anh hùng lao động", 2, 0),
        ("Đảng viên có Huy hiệu 40 tuổi Đảng trở lên", 3, 0),
        ("Người hoạt động cách mạng trước ngày 01/01/1945\n(cán bộ lão thành cách mạng)", 4, 0),
        ("Người hoạt động cách mạng từ ngày 01/01/1945\nđến trước Tổng khởi nghĩa 19/8/1945 (Cán bộ tiền khởi nghĩa)", 5, 0),
        ("Thương binh, người hưởng chính sách như thương binh\ncó tỷ lệ thương tật từ 81% trở lên", 6, 0),
        ("Bệnh binh có tỷ lệ suy giảm khả năng lao động từ 81% trở lên", 7, 0),
        ("Người hoạt động kháng chiến bị nhiễm chất độc hóa học\ncó tỷ lệ suy giảm khả năng lao động từ 81% trở lên", 8, 0),
        ("Thân nhân liệt sĩ và người có công giúp đỡ cách mạng đang hưởng\ntrợ cấp hàng tháng định suất nuôi dưỡng (già yếu, neo đơn)", 9, 0),
        ("Các đối tượng chính sách đang được nuôi dưỡng\ntại Trung tâm dưỡng lão Thị Nghè", 10, 0),
        ("Hộ nghèo (theo tiêu chí của Thành phố), mã số: {{cremation_poor_detail}}", 11, 0),
    )
    for text, number, level in options_page_1:
        _option(document, text, f"cremation_category_{number}_check", level=level)
    social = _paragraph(document)
    social.paragraph_format.left_indent = Cm(1.0)
    _font(social.add_run("- Các đối tượng đang hưởng trợ cấp xã hội hàng tháng tại phường:"))
    _option(document, "Người khuyết tật (theo Nghị định số 28/2012/NĐ-CP)", "cremation_category_12_check", level=1)

    page_break = _paragraph(document)
    page_break.add_run().add_break(WD_BREAK.PAGE)
    _option(document, "Người cao tuổi (theo Nghị định số 20/2021/NĐ-CP)", "cremation_category_13_check", level=1)
    _option(document, "Đối tượng bảo trợ xã hội khác (theo Nghị định số 67/2007/NĐ-CP,\nNghị định số 13/2010/NĐ-CP hoặc Nghị định số 136/2013/NĐ-CP)", "cremation_category_14_check", level=1)
    _option(document, "Đối tượng hưu trí", "cremation_category_15_check")
    _option(document, "Hộ cận nghèo (theo tiêu chí của Thành phố), mã số: {{cremation_near_poor_detail}}", "cremation_category_16_check")
    _option(document, "Người dân có hộ khẩu tại Thành phố Hồ Chí Minh", "cremation_category_17_check")
    children = _paragraph(document)
    children.paragraph_format.left_indent = Cm(1.0)
    _font(children.add_run("- Trẻ từ 6 tuổi trở xuống:"))
    _option(document, "Có hộ khẩu tại Thành phố Hồ Chí Minh", "cremation_category_18_check", level=1)
    _option(document, "Có tạm trú (KT3) tại Thành phố Hồ Chí Minh", "cremation_category_19_check", level=1, after=2)

    declaration = _paragraph(document, after=2)
    declaration.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    declaration.paragraph_format.first_line_indent = Cm(1.0)
    _font(declaration.add_run("Tôi xin cam đoan những lời khai trên đây là đúng sự thực, nếu có điều gì khai không đúng sự thật tôi xin chịu trách nhiệm hoàn toàn trước pháp luật."))
    request = _paragraph(document, after=2)
    request.paragraph_format.first_line_indent = Cm(1.0)
    _font(request.add_run("Đề nghị Ủy ban nhân dân phường hỗ trợ chi phí khuyến khích hỏa táng."))

    signature = document.add_table(rows=1, cols=2)
    signature.autofit = False
    _remove_table_borders(signature)
    signature.columns[0].width = Cm(8.0)
    signature.columns[1].width = Cm(8.7)
    right = signature.cell(0, 1)
    right.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    for index, (text, bold, italic) in enumerate((
        ("Ngày {{declaration_day}} tháng {{declaration_month}} năm {{declaration_year}}", False, True),
        ("Người khai", True, False),
        ("(ký và ghi rõ họ, tên)", False, True),
        ("", False, False),
        ("{{full_name}}", True, False),
    )):
        paragraph = right.paragraphs[0] if index == 0 else right.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_after = Pt(0)
        _font(paragraph.add_run(text), bold=bold, italic=italic)

    separator = _paragraph(document, after=1)
    border = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "000000")
    border.append(bottom)
    separator._p.get_or_add_pPr().append(border)

    for text in (
        "Xác nhận của UBND phường Minh Phụng:",
        "Ông (bà) (1) ..................................................................... , sinh năm ...............................",
        "Hiện cư trú tại: .................................................................................................................",
        "Là (2) ........................................ ........................................................................................",
        "(hoặc đại diện: ........................................ ...................................................................... )",
        "của ông (bà) (3) ...................................................................................................................",
        "thuộc đối tượng (4) ..............................................................................................................",
        "................................................................................................................................................",
        "đã chết ngày…….tháng…….năm ……………",
        "Đề nghị được giải quyết chế độ hỗ trợ chi phí khuyến khích hỏa táng./.",
    ):
        paragraph = _paragraph(document)
        _font(paragraph.add_run(text), size=11.5)

    chair = document.add_table(rows=1, cols=2)
    chair.autofit = False
    _remove_table_borders(chair)
    chair.columns[0].width = Cm(8.0)
    chair.columns[1].width = Cm(8.7)
    right = chair.cell(0, 1)
    for index, (text, bold, italic) in enumerate((("…………, ngày......tháng......năm 20...", False, True), ("Chủ tịch", True, False))):
        paragraph = right.paragraphs[0] if index == 0 else right.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_after = Pt(0)
        _font(paragraph.add_run(text), size=11.5, bold=bold, italic=italic)

    notes = (
        "(1) Đối tượng thực hiện thủ tục hành chính tại mục 1;",
        "(2) Mối quan hệ nhân thân được thể hiện tại mục 4;",
        "(3) Đối tượng được nêu tại mục 5;",
        "(4) Đối tượng được nêu tại mục 7.",
    )
    for text in notes:
        paragraph = _paragraph(document)
        paragraph.paragraph_format.left_indent = Cm(0.1)
        _font(paragraph.add_run(text), size=10)

    document.core_properties.title = "Tờ khai nhận chi phí hỗ trợ khuyến khích hỏa táng"
    document.core_properties.author = "Auto Form"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    document.save(OUTPUT)
    return OUTPUT


if __name__ == "__main__":
    print(build_template())
