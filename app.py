import io
import re
import streamlit as st

import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

st.set_page_config(page_title="Invoice - Remove Price (Cloud Safe)", layout="wide")
st.title("Invoice PDF – Remove Price")
#st.caption("Che trắng phần giá để gửi sale (ổn định trên Streamlit Cloud). Output: (tên file)_tosale.pdf")

uploaded = st.file_uploader(
    "Upload invoice PDF (có thể chọn nhiều file)",
    type=["pdf"],
    accept_multiple_files=True
)

# Token cần che: USD / số tiền có dấu phẩy hoặc thập phân / gạch placeholder
PRICE_TOKEN = re.compile(r"^(USD|[\d,]+(\.\d+)?|[_\-—]+)$")

# Không được đụng tới dòng PAYMENT TERM
FORBIDDEN_LINE_KEYWORDS = {"PAYMENT", "TERM", "O/A", "DAYS"}

TOTAL_LABELS = {"SUB", "TOTAL", "AMOUNT", "TAX", "WEEE"}  # để dò vùng totals


def group_by_line(words, y_tol=2.0):
    """
    pdfplumber words: dict {text,x0,x1,top,bottom,...}
    group theo dòng dựa trên 'top'
    """
    words_sorted = sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
    lines = []
    for w in words_sorted:
        placed = False
        for line in lines:
            if abs(line["top"] - w["top"]) <= y_tol:
                line["words"].append(w)
                placed = True
                break
        if not placed:
            lines.append({"top": w["top"], "words": [w]})
    # sort words inside each line by x0
    for line in lines:
        line["words"].sort(key=lambda w: w["x0"])
    return lines


def find_line_containing(lines, must_have):
    """
    must_have: set of tokens uppercase to exist in line text
    return first matched line
    """
    for line in lines:
        tokens = {w["text"].upper() for w in line["words"]}
        if must_have.issubset(tokens):
            return line
    return None


def find_x_of_unit_price(lines):
    """
    tìm x0 của cụm UNIT PRICE trong line header
    """
    for line in lines:
        t = [w["text"].upper() for w in line["words"]]
        if "UNIT" in t and "PRICE" in t:
            # lấy x0 nhỏ nhất của chữ UNIT/PRICE
            xs = [w["x0"] for w in line["words"] if w["text"].upper() in {"UNIT", "PRICE"}]
            return min(xs) if xs else None
    return None


def find_table_y_bounds(lines, page_height):
    """
    y0: sau line có ITEM/DESCRIPTION...
    y1: trước line có TOTAL QUANTITY
    pdfplumber dùng top/bottom với gốc trên, reportlab gốc dưới.
    """
    header_line = None
    for line in lines:
        tokens = {w["text"].upper() for w in line["words"]}
        if "ITEM" in tokens and "DESCRIPTION" in tokens:
            header_line = line
            break

    total_qty_line = None
    for line in lines:
        tokens = {w["text"].upper() for w in line["words"]}
        if "TOTAL" in tokens and "QUANTITY" in tokens:
            total_qty_line = line
            break

    if not header_line or not total_qty_line:
        return None, None

    y0_top = header_line["top"]  # top of header line
    y1_top = total_qty_line["top"]

    # convert to reportlab coordinates (bottom-left origin)
    table_top = page_height - (y0_top - 6)          # start a bit above header
    table_bottom = page_height - (y1_top + 10)      # stop a bit above TOTAL QUANTITY line
    return table_bottom, table_top  # (ymin, ymax)


def create_overlay_pdf(pages_rects, page_sizes):
    """
    pages_rects: list of list rects per page, rect = (x0, y0, x1, y1) in reportlab coords
    page_sizes: list of (w,h)
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf)

    for i, rects in enumerate(pages_rects):
        w, h = page_sizes[i]
        c.setPageSize((w, h))
        c.setFillColorRGB(1, 1, 1)
        c.setStrokeColorRGB(1, 1, 1)

        for (x0, y0, x1, y1) in rects:
            rw = max(0, x1 - x0)
            rh = max(0, y1 - y0)
            if rw > 0 and rh > 0:
                c.rect(x0, y0, rw, rh, fill=1, stroke=0)

        c.showPage()

    c.save()
    buf.seek(0)
    return buf


def redact_invoice_cloud_safe(pdf_bytes: bytes):
    # read original pdf
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()

    pages_rects = []
    page_sizes = []

    # extract words + decide rects
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for p_idx, page in enumerate(pdf.pages):
            w, h = float(page.width), float(page.height)
            page_sizes.append((w, h))

            words = page.extract_words(
                keep_blank_chars=False,
                use_text_flow=True
            )
            lines = group_by_line(words, y_tol=2.0)

            unit_x0 = find_x_of_unit_price(lines)
            ymin, ymax = find_table_y_bounds(lines, h)

            rects = []

            # Nếu không tìm được layout, vẫn output y nguyên (không che)
            if unit_x0 is None or ymin is None or ymax is None:
                pages_rects.append(rects)
                continue

            for line in lines:
                tokens = [w["text"].upper() for w in line["words"]]
                token_set = set(tokens)

                # skip PAYMENT TERM line
                if token_set & FORBIDDEN_LINE_KEYWORDS:
                    continue

                # convert line top to reportlab coord to filter within table y-range
                line_y = h - line["top"]
                if not (ymin <= line_y <= ymax):
                    continue

                # che các token giá ở bên phải cột UNIT PRICE
                for wd in line["words"]:
                    if wd["x0"] < unit_x0 - 1:
                        continue

                    t = wd["text"]
                    if PRICE_TOKEN.match(t):
                        # convert pdfplumber coords (top-origin) to reportlab coords (bottom-origin)
                        x0 = wd["x0"] - 1
                        x1 = wd["x1"] + 1
                        y0 = h - wd["bottom"] - 1
                        y1 = h - wd["top"] + 1
                        rects.append((x0, y0, x1, y1))

                # SAY TOTAL: che phần sau "SAY" "TOTAL"
                if "SAY" in token_set and "TOTAL" in token_set:
                    # find x after "TOTAL"
                    xs = [w["x1"] for w in line["words"] if w["text"].upper() == "TOTAL"]
                    cut_x = max(xs) if xs else None
                    if cut_x:
                        for wd in line["words"]:
                            if wd["x0"] > cut_x + 2:
                                x0 = wd["x0"] - 1
                                x1 = wd["x1"] + 1
                                y0 = h - wd["bottom"] - 1
                                y1 = h - wd["top"] + 1
                                rects.append((x0, y0, x1, y1))

            pages_rects.append(rects)

    # build overlay pdf
    overlay_buf = create_overlay_pdf(pages_rects, page_sizes)
    overlay_reader = PdfReader(overlay_buf)

    # merge overlay onto original pages
    for i, page in enumerate(reader.pages):
        if i < len(overlay_reader.pages):
            page.merge_page(overlay_reader.pages[i])
        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out.getvalue()


if uploaded:
    st.subheader("Kết quả")
    for f in uploaded:
        cleaned = redact_invoice_cloud_safe(f.read())

        base = f.name.rsplit(".", 1)[0]
        out_name = f"{base}_tosale.pdf"

        st.download_button(
            label=f"Download: {out_name}",
            data=cleaned,
            file_name=out_name,
            mime="application/pdf",
            key=f"dl_{f.name}"
        )
else:
    st.info("Upload invoice PDF")
