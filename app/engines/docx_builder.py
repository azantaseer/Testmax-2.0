"""Word file of a test case set, ported from Pilot 1 (docx_builder.py). Only change: returns the bytes instead of writing a file."""
import io

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

HEADER_BG = "1F2A37"
HEADER_TEXT = RGBColor(0xFF, 0xFF, 0xFF)
ROW_ALT_BG = "F1F5F9"
BODY_FONT = "Calibri"
TITLE_COLOR = RGBColor(0x1F, 0x2A, 0x37)
ACCENT_COLOR = RGBColor(0x25, 0x63, 0xEB)

COLUMN_HEADERS = ("Sr. No.", "Test Step", "Field Name", "Test Data", "Expected Result")
COLUMN_WIDTHS_CM = (1.6, 7.5, 4.0, 4.0, 9.6)  # sums to usable width on A4 landscape w/ 1.5cm margins


def _set_cell_background(cell, hex_color: str) -> None:
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), hex_color)
    cell._tc.get_or_add_tcPr().append(shading)


def _set_row_header_repeat(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


def _set_row_cant_split(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    cant_split = OxmlElement("w:cantSplit")
    tr_pr.append(cant_split)


def _set_cell_width(cell, width: Cm) -> None:
    cell.width = width
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = OxmlElement("w:tcW")
    tc_w.set(qn("w:w"), str(int(width.twips)))
    tc_w.set(qn("w:type"), "dxa")
    tc_pr.append(tc_w)


def _set_table_cell_margins(table, *, top_dxa: int, bottom_dxa: int, left_dxa: int, right_dxa: int) -> None:
    """Tightens the table's default per-cell padding so rows take noticeably
    less vertical space, reducing the chance that a short table's last row
    or two spill onto an almost-empty continuation page."""
    tbl_pr = table._tbl.tblPr
    margins = OxmlElement("w:tblCellMar")
    for tag, value in (("top", top_dxa), ("bottom", bottom_dxa), ("start", left_dxa), ("end", right_dxa)):
        node = OxmlElement(f"w:{tag}")
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")
        margins.append(node)
    tbl_pr.append(margins)


def _set_cell_text(cell, text: str, *, bold: bool = False, color: RGBColor | None = None, size: int = 10, align=None) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    if align is not None:
        paragraph.alignment = align
    run = paragraph.add_run(text if text is not None else "")
    run.font.name = BODY_FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def _add_field_code(paragraph, instr_text: str) -> None:
    run = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instr_text
    fld_separate = OxmlElement("w:fldChar")
    fld_separate.set(qn("w:fldCharType"), "separate")
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(fld_separate)
    run._r.append(fld_end)


def _add_footer_page_numbers(document: Document) -> None:
    footer = document.sections[0].footer
    paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run("Page ")
    run.font.name = BODY_FONT
    run.font.size = Pt(9)
    _add_field_code(paragraph, "PAGE")
    run2 = paragraph.add_run(" of ")
    run2.font.name = BODY_FONT
    run2.font.size = Pt(9)
    _add_field_code(paragraph, "NUMPAGES")


def _configure_landscape_a4(document: Document) -> None:
    section = document.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width = Cm(29.7)
    section.page_height = Cm(21.0)
    section.top_margin = Cm(1.5)
    section.bottom_margin = Cm(1.5)
    section.left_margin = Cm(1.5)
    section.right_margin = Cm(1.5)


def _add_labeled_paragraph(document: Document, label: str, value: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(2)
    paragraph.paragraph_format.keep_with_next = True
    label_run = paragraph.add_run(f"{label}: ")
    label_run.bold = True
    label_run.font.name = BODY_FONT
    label_run.font.size = Pt(10.5)
    value_run = paragraph.add_run(value or "—")
    value_run.font.name = BODY_FONT
    value_run.font.size = Pt(10.5)


def _add_prerequisites_list(document: Document, pre_reqs: list) -> None:
    """Renders Pre-Requisites as separate readable list items instead of a
    single sentence joined with semicolons."""
    label_paragraph = document.add_paragraph()
    label_paragraph.paragraph_format.space_before = Pt(0)
    label_paragraph.paragraph_format.space_after = Pt(2)
    label_paragraph.paragraph_format.keep_with_next = True
    label_run = label_paragraph.add_run("Pre-Requisites:")
    label_run.bold = True
    label_run.font.name = BODY_FONT
    label_run.font.size = Pt(10.5)

    items = pre_reqs or []
    if not items:
        empty_paragraph = document.add_paragraph()
        empty_paragraph.paragraph_format.space_before = Pt(0)
        empty_paragraph.paragraph_format.space_after = Pt(3)
        empty_paragraph.paragraph_format.keep_with_next = True
        empty_run = empty_paragraph.add_run("—")
        empty_run.font.name = BODY_FONT
        empty_run.font.size = Pt(10.5)
        return

    for index, item in enumerate(items):
        bullet_paragraph = document.add_paragraph(style="List Bullet")
        bullet_paragraph.paragraph_format.space_before = Pt(0)
        bullet_paragraph.paragraph_format.space_after = Pt(2) if index == len(items) - 1 else Pt(0)
        bullet_paragraph.paragraph_format.keep_with_next = True
        bullet_run = bullet_paragraph.add_run(item)
        bullet_run.font.name = BODY_FONT
        bullet_run.font.size = Pt(10.5)


def _add_step_table(document: Document, steps: list) -> None:
    table = document.add_table(rows=1, cols=5)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    _set_table_cell_margins(table, top_dxa=24, bottom_dxa=24, left_dxa=80, right_dxa=80)

    header_row = table.rows[0]
    _set_row_header_repeat(header_row)
    for cell, header_text, width_cm in zip(header_row.cells, COLUMN_HEADERS, COLUMN_WIDTHS_CM):
        _set_cell_background(cell, HEADER_BG)
        _set_cell_text(cell, header_text, bold=True, color=HEADER_TEXT, size=10)
        _set_cell_width(cell, Cm(width_cm))

    for index, step in enumerate(steps):
        row = table.add_row()
        _set_row_cant_split(row)
        if index % 2 == 1:
            for cell in row.cells:
                _set_cell_background(cell, ROW_ALT_BG)

        values = (
            str(step["stepNo"]),
            step["testStep"],
            step["fieldName"],
            step["testData"],
            step["expectedResult"],
        )
        for cell, value, width_cm in zip(row.cells, values, COLUMN_WIDTHS_CM):
            _set_cell_text(cell, value, size=10)
            _set_cell_width(cell, Cm(width_cm))


def _add_test_case_section(document: Document, test_case: dict) -> None:
    document.add_page_break()

    heading = document.add_heading(level=1)
    heading.paragraph_format.space_after = Pt(3)
    heading.paragraph_format.keep_with_next = True
    run = heading.add_run(f"{test_case['id']} — {test_case['testCaseType']}")
    run.font.color.rgb = ACCENT_COLOR
    run.font.name = BODY_FONT
    run.font.size = Pt(15)

    title_paragraph = document.add_paragraph()
    title_paragraph.paragraph_format.space_before = Pt(0)
    title_paragraph.paragraph_format.space_after = Pt(3)
    title_paragraph.paragraph_format.keep_with_next = True
    title_run = title_paragraph.add_run(test_case["title"])
    title_run.bold = True
    title_run.font.size = Pt(12)
    title_run.font.name = BODY_FONT

    _add_labeled_paragraph(document, "Overview", test_case["overview"])
    _add_prerequisites_list(document, test_case["preRequisites"])
    _add_labeled_paragraph(document, "Test Objective", test_case["testObjective"])
    _add_labeled_paragraph(document, "Expected Result", test_case["expectedResult"])

    _add_step_table(document, test_case["steps"])


def _add_summary_section(document: Document, test_cases: list, not_generated: list | None = None) -> None:
    document.add_page_break()
    heading = document.add_heading(level=1)
    run = heading.add_run("Test Case Summary")
    run.font.color.rgb = ACCENT_COLOR
    run.font.name = BODY_FONT
    run.font.size = Pt(15)

    table = document.add_table(rows=1, cols=3)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    _set_table_cell_margins(table, top_dxa=40, bottom_dxa=40, left_dxa=80, right_dxa=80)
    header_row = table.rows[0]
    _set_row_header_repeat(header_row)
    headers = ("Category", "Count", "Test Cases")
    widths = (4.0, 2.5, 20.2)
    for cell, text, width_cm in zip(header_row.cells, headers, widths):
        _set_cell_background(cell, HEADER_BG)
        _set_cell_text(cell, text, bold=True, color=HEADER_TEXT, size=10)
        _set_cell_width(cell, Cm(width_cm))

    for index, category in enumerate(("POSITIVE", "NEGATIVE", "VERIFICATION", "BOUNDARY", "EDGE")):
        in_category = [tc for tc in test_cases if tc["testCaseType"] == category]
        row = table.add_row()
        _set_row_cant_split(row)
        if index % 2 == 1:
            for cell in row.cells:
                _set_cell_background(cell, ROW_ALT_BG)
        values = (category, str(len(in_category)), ", ".join(f"{tc['id']}" for tc in in_category) or "—")
        for cell, value, width_cm in zip(row.cells, values, widths):
            _set_cell_text(cell, value, size=10)
            _set_cell_width(cell, Cm(width_cm))

    if not_generated:
        document.add_paragraph()
        sub_heading = document.add_paragraph()
        sub_run = sub_heading.add_run("Not generated — the application scan is missing a control these tests need")
        sub_run.bold = True
        sub_run.font.name = BODY_FONT
        sub_run.font.size = Pt(11)
        for entry in not_generated:
            paragraph = document.add_paragraph(style="List Bullet")
            run = paragraph.add_run(
                f"{entry.get('title', '')} — missing: {entry.get('missingElement', '')}. {entry.get('reason', '')}"
            )
            run.font.name = BODY_FONT
            run.font.size = Pt(10)


def build_test_cases_docx(
    *,
    requirement_id: str,
    scan_run_id: str,
    generation_run_id: str,
    model: str,
    generated_at: str,
    test_cases: list,
    not_generated: list | None = None,
) -> bytes:
    document = Document()
    _configure_landscape_a4(document)

    style = document.styles["Normal"]
    style.font.name = BODY_FONT
    style.font.size = Pt(10.5)

    title = document.add_heading(level=0)
    title_run = title.add_run("TestMax Test Cases")
    title_run.font.name = BODY_FONT
    title_run.font.color.rgb = TITLE_COLOR

    _add_labeled_paragraph(document, "Requirement ID", requirement_id)
    _add_labeled_paragraph(document, "Source Scan Run", scan_run_id)
    _add_labeled_paragraph(document, "Generation Model", model)
    _add_labeled_paragraph(document, "Generated At", generated_at)
    _add_labeled_paragraph(document, "Test Case Count", str(len(test_cases)))

    _add_footer_page_numbers(document)

    # Every test case gets the same full page budget, including the first —
    # otherwise TC-001 would share page 1 with this cover block and have
    # noticeably less room than every later case, making it the one most
    # likely to spill a row or two onto a near-empty continuation page.
    for test_case in test_cases:
        _add_test_case_section(document, test_case)

    _add_summary_section(document, test_cases, not_generated=not_generated)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
