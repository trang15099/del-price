import io
import re
import streamlit as st
import fitz  # PyMuPDF

st.set_page_config(page_title="Invoice - Remove Price", layout="wide")
st.title("Invoice PDF – Remove Price (ASUS Safe Mode)")
#st.caption("Xóa giá bán cho sale: KHÔNG xóa PAYMENT TERM, KHÔNG xóa TOTAL AMOUNT label")

uploaded = st.file_uploader(
    "Upload invoice PDF",
    type=["pdf"],
    accept_multiple_files=True
)

# Nhận diện token giá / placeholder
PRICE_TOKEN = re.compile(
    r"^(USD|[\d,]+(?:\.\d+)?|[_\-—]+)$"
)

# Các dòng không được đụng tới dù có số
FORBIDDEN_LINE_KEYWORDS = [
    "PAYMENT TERM",
    "DAYS",
    "O/A",
]

def redact_like_sample(pdf_bytes: bytes) -> bytes:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    for page in doc:
        words = page.get_text("words")
        # words: x0, y0, x1, y1, text, block, line, word_no

        # Gom words theo dòng
        lines = {}
        for x0, y0, x1, y1, w, bno, lno, _ in words:
            key = (bno, lno)
            lines.setdefault(key, []).append((x0, y0, x1, y1, w))

        # Xác định vùng bảng giá theo trục Y
        hdr = page.search_for("ITEM")
        qty = page.search_for("TOTAL QUANTITY")

        if not hdr or not qty:
            continue

        table_y0 = hdr[0].y1 + 2
        table_y1 = qty[0].y0 - 2

        # Xác định cột giá theo trục X
        unit_hdr = page.search_for("UNIT PRICE")
        if not unit_hdr:
            continue

        price_x0 = unit_hdr[0].x0 - 2

        def redact_word(x0, y0, x1, y1):
            r = fitz.Rect(x0 - 1, y0 - 1, x1 + 1, y1 + 1)
            page.add_redact_annot(r, fill=(1, 1, 1))

        for (bno, lno), ws in lines.items():
            line_text = " ".join(w for *_, w in ws)

            # Skip các dòng bị cấm
            if any(k in line_text for k in FORBIDDEN_LINE_KEYWORDS):
                continue

            for x0, y0, x1, y1, w in ws:
                # Chỉ xử lý trong bảng giá
                if not (table_y0 <= y0 <= table_y1):
                    continue

                # Chỉ xử lý bên phải UNIT PRICE
                if x0 < price_x0:
                    continue

                # Token giá / gạch
                if PRICE_TOKEN.match(w):
                    redact_word(x0, y0, x1, y1)

        # SAY TOTAL: chỉ xóa nội dung sau dấu :
        say = page.search_for("SAY TOTAL")
        for r in say:
            for (bno, lno), ws in lines.items():
                ly0 = min(t[1] for t in ws)
                if abs(ly0 - r.y0) < 3:
                    for x0, y0, x1, y1, w in ws:
                        if x0 > r.x1 + 3:
                            redact_word(x0, y0, x1, y1)

        page.apply_redactions()

    out = io.BytesIO()
    doc.save(out, deflate=True, garbage=4)
    doc.close()
    return out.getvalue()


if uploaded:
    st.subheader("Download")
    for f in uploaded:
        cleaned = redact_like_sample(f.read())
        base = f.name.rsplit(".", 1)[0]
        output_name = f"{base}_tosale.pdf"

        st.download_button(
            label=f"Download: {output_name}",
            data=cleaned,
            file_name=output_name,
            mime="application/pdf",
            key=f.name
        )
else:
    st.info("Upload invoice PDF")
