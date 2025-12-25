import io
import re
import streamlit as st

import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas


# =========================
# CONFIG
# =========================
st.set_page_config(page_title="Invoice - Remove Price (Cloud Safe)", layout="wide")
st.title("Invoice PDF – Remove Price (Cloud Safe)")
st.caption("Che trắng phần giá để gửi sale. Output: (tên file)_tosale.pdf")

uploaded = st.file_uploader(
    "Upload invoice PDF (có thể chọn nhiều file)",
    type=["pdf"],
    accept_multiple_files=True
)

# Token cần che trong vùng giá:
# - USD
# - số tiền dạng 1,234.56 hoặc 1234.56 hoặc 1234
# - gạch placeholder như ___ hoặc —— hoặc ----
PRICE_TOKEN = re.compile(r"^(USD|[\d,]+(\.\d+)?|[_\-—]+)$")

# Không đụng các dòng có cụm này (tránh xóa 45 trong PAYMENT TERM)
FORBIDDEN_LINE_KEYWORDS = {"PAYMENT", "TERM", "O/A", "DAYS"}


# =========================
# HELPERS
# =========================
def group_by_line(words, y_tol=2.0):
    """
    pdfplumber words: dict {text,x0,x1,top,bottom,...}
    Group theo dòng dựa trên 'top' (gốc tọa độ ở phía trên).
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

    for line in lines:
        line["words"].sort(key=lambda w: w["x0"])
    return lines


def find_x_of_unit_price(lines):
    """
    Tìm mốc x0 của cột UNIT PRICE.
    """
    for line in lines:
        tokens = [w["text"].upper() for w in line["words"]]
        if "UNIT" in tokens and "PRICE" in tokens:
            xs = [w["x0"] for w in line["words"] if w["text"].upper() in {"UNIT", "PRICE"}]
            return min(xs) if xs else None
    return None


def find_table_y_bounds(lines, page_height):
    """
    Giới hạn vùng xử lý theo trục Y:
    - Bắt đầu sau header có ITEM + DESCRIPTION
    - Kết thúc trước dòng TOTAL + QUANTITY

    Trả về (ymin, ymax) theo hệ tọa độ reportlab (gốc dưới-trái).
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

    y0_top = header_line["top"]
    y1_top = total_qty_line["top"]

    # Convert sang reportlab coords (bottom-left origin)
    table_top = page_height - (y0_top - 6)      # nới lên chút
    table_bottom = page_height - (y1_top + 10)  # nới xuống chút
    return table_bottom, table_top


def create_overlay_pdf(pages_rects, page_sizes):
    """
    Vẽ các hình chữ nhật trắng lên từng trang (overlay PDF).
    pages_rects: list[ list[(x0,y0,x1,y1)] ] theo reportlab coords
    page_sizes: list[(w,h)]
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


def build_redaction_rects(pdf_bytes: bytes):
    """
    Trích xuất words bằng pdfplumber -> tính danh sách rects cần che cho từng trang.
    Chỉ che:
    - Trong vùng bảng (từ header ITEM/DESCRIPTION đến trước TOTAL QUANTITY)
    - Ở bên phải cột UNIT PRICE
    - Skip dòng PAYMENT TERM
    - Che token: USD, số tiền, gạch placeholder
    - Che phần sau SAY TOTAL:
    """
    pages_rects = []
    page_sizes = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            w, h = float(page.width), float(page.height)
            page_sizes.append((w, h))

            words = page.extract_words(keep_blank_chars=False, use_text_flow=True)
            lines = group_by_line(words, y_tol=2.0)

            unit_x0 = find_x_of_unit_price(lines)
            ymin, ymax = find_table_y_bounds(lines, h)

            rects = []
            if unit_x0 is None or ymin is None or ymax is None:
                pages_rects.append(rects)
                continue

            for line in lines:
                tokens = [wd["text"].upper() for wd in line["words"]]
                token_set = set(tokens)

                # Skip dòng PAYMENT TERM (không xóa số 45, v.v.)
                if token_set & FORBIDDEN_LINE_KEYWORDS:
                    continue

                # Chỉ xử lý trong vùng bảng
                line_y = h - line["top"]  # reportlab y
                if not (ymin <= line_y <= ymax):
                    continue

                # Che token giá ở bên phải UNIT PRICE
                for wd in line["words"]:
                    if wd["x0"] < unit_x0 - 1:
                        continue

                    t = wd["text"]
                    if PRICE_TOKEN.match(t):
                        # pdfplumber: top-origin => convert to reportlab bottom-origin
                        x0 = wd["x0"] - 1
                        x1 = wd["x1"] + 1
                        y0 = h - wd["bottom"] - 1
                        y1 = h - wd["top"] + 1
                        rects.append((x0, y0, x1, y1))

                # SAY TOTAL: che phần nội dung sau chữ TOTAL trên cùng dòng
                if "SAY" in token_set and "TOTAL" in token_set:
                    xs = [wd["x1"] for wd in line["words"] if wd["text"].upper() == "TOTAL"]
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

    return pages_rects, page_sizes


def redact_invoice_cloud_safe(pdf_bytes: bytes) -> bytes:
    """
    Tạo overlay che trắng và merge lên PDF gốc (giữ nguyên kích thước trang).
    """
    pages_rects, page_sizes = build_redaction_rects(pdf_bytes)

    overlay_buf = create_overlay_pdf(pages_rects, page_sizes)
    overlay_reader = PdfReader(overlay_buf)

    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()

    for i, page in enumerate(reader.pages):
        if i < len(overlay_reader.pages):
            page.merge_page(overlay_reader.pages[i])
        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out.getvalue()


# =========================
# UI
# =========================
if uploaded:
    st.subheader("Download")
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
    st.info("Upload invoice PDF để bắt đầu.")
