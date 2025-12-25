import io
import re
import streamlit as st

import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas


# =========================
# UI
# =========================
st.set_page_config(page_title="Invoice – Remove Price", layout="wide")
st.title("Invoice PDF – Remove Price")
st.caption("Chỉ xử lý PDF dạng text. Output: (tên file)_tosale.pdf")

uploaded = st.file_uploader(
    "Upload Commercial Invoice PDF (có thể chọn nhiều file)",
    type=["pdf"],
    accept_multiple_files=True
)

# =========================
# RULES
# =========================
PRICE_TOKEN = re.compile(r"^(USD|[\d,]+(\.\d+)?|[_\-—]+)$")

FORBIDDEN_LINE_KEYWORDS = {"PAYMENT", "TERM", "O/A", "DAYS"}

TOTAL_LABEL_KEYWORDS = {
    "SUB", "TOTAL", "AMOUNT", "TAX", "RATE", "WEEE"
}


# =========================
# HELPERS
# =========================
def group_by_line(words, y_tol=2.5):
    words = sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
    lines = []
    for w in words:
        for line in lines:
            if abs(line["top"] - w["top"]) <= y_tol:
                line["words"].append(w)
                break
        else:
            lines.append({"top": w["top"], "words": [w]})
    for l in lines:
        l["words"].sort(key=lambda w: w["x0"])
    return lines


def find_unit_price_x(lines):
    for line in lines:
        tokens = [w["text"].upper() for w in line["words"]]
        if "UNIT" in tokens and "PRICE" in tokens:
            xs = [w["x0"] for w in line["words"] if w["text"].upper() in {"UNIT", "PRICE"}]
            return min(xs)
    return None


def find_line_top(lines, must_have):
    for line in lines:
        if must_have.issubset({w["text"].upper() for w in line["words"]}):
            return line["top"]
    return None


def to_rect(wd, page_h, pad=1.2):
    return (
        wd["x0"] - pad,
        page_h - wd["bottom"] - pad,
        wd["x1"] + pad,
        page_h - wd["top"] + pad,
    )


def build_overlay(rects_by_page, sizes):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setFillColorRGB(1, 1, 1)
    c.setStrokeColorRGB(1, 1, 1)

    for i, rects in enumerate(rects_by_page):
        w, h = sizes[i]
        c.setPageSize((w, h))
        for x0, y0, x1, y1 in rects:
            c.rect(x0, y0, max(0, x1 - x0), max(0, y1 - y0), fill=1, stroke=0)
        c.showPage()

    c.save()
    buf.seek(0)
    return buf


# =========================
# CORE
# =========================
def redact_invoice(pdf_bytes: bytes) -> bytes:
    rects_all = []
    sizes = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            w, h = float(page.width), float(page.height)
            sizes.append((w, h))

            words = page.extract_words(use_text_flow=True)
            if not words:
                rects_all.append([])
                continue

            lines = group_by_line(words)

            unit_x = find_unit_price_x(lines)
            if unit_x is None:
                rects_all.append([])
                continue

            header_top = find_line_top(lines, {"ITEM"})
            qty_top = find_line_top(lines, {"TOTAL", "QUANTITY"})
            say_top = find_line_top(lines, {"SAY", "TOTAL"})

            if header_top is None:
                header_top = 0
            if qty_top is None:
                qty_top = say_top if say_top else h

            table_ymin = h - (qty_top + 8)
            table_ymax = h - (header_top - 8)

            rects = []

            for line in lines:
                tokens = {w["text"].upper() for w in line["words"]}
                line_y = h - line["top"]

                if tokens & FORBIDDEN_LINE_KEYWORDS:
                    continue

                # ===== 1. UNIT PRICE + LINE TOTAL =====
                if table_ymin <= line_y <= table_ymax:
                    for wd in line["words"]:
                        if wd["x0"] >= unit_x and PRICE_TOKEN.match(wd["text"]):
                            rects.append(to_rect(wd, h))

                # ===== 2. TOTALS BLOCK =====
                if tokens & TOTAL_LABEL_KEYWORDS and "AMOUNT" in tokens:
                    for wd in line["words"]:
                        if wd["x0"] >= unit_x and PRICE_TOKEN.match(wd["text"]):
                            rects.append(to_rect(wd, h))

                # ===== 3. SAY TOTAL (LUÔN XÓA) =====
                if {"SAY", "TOTAL"}.issubset(tokens):
                    cut_x = 0
                    for wd in line["words"]:
                        if ":" in wd["text"] or wd["text"].upper() == "TOTAL":
                            cut_x = max(cut_x, wd["x1"] + 2)
                    for wd in line["words"]:
                        if wd["x0"] > cut_x:
                            rects.append(to_rect(wd, h))

            rects_all.append(rects)

    overlay = build_overlay(rects_all, sizes)
    overlay_reader = PdfReader(overlay)
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
# RUN
# =========================
if uploaded:
    for f in uploaded:
        cleaned = redact_invoice(f.read())
        name = f.name.rsplit(".", 1)[0] + "_tosale.pdf"
        st.download_button(
            label=f"Download: {name}",
            data=cleaned,
            file_name=name,
            mime="application/pdf",
            key=name
        )
else:
    st.info("Upload invoice PDF để bắt đầu.")
