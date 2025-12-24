import io
import re
import streamlit as st
import fitz  # PyMuPDF

st.set_page_config(page_title="Invoice - Remove Price", layout="wide")
st.title("Invoice PDF - Xóa giá")

uploaded = st.file_uploader("Upload invoice PDF", type=["pdf"], accept_multiple_files=True)

# Lề an toàn nhỏ để che đúng chữ, không đè lên line
#MARGIN = st.slider("Lề an toàn (px)", 0, 8, 2)

# Nhận diện token tiền: USD + các số dạng 151,308.00 / 467.0000000 / 0.00 / 0
MONEY_TOKEN = re.compile(r"^(USD|\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+\.\d+|\d+)$")

TOTAL_LABELS = [
    "SUB TOTAL AMOUNT",
    "TAX AMOUNT",
    "TOTAL WEEE AMOUNT",
    "TOTAL AMOUNT",
]

def redact_like_sample(pdf_bytes: bytes, margin: int = 2) -> bytes:
    """
    Xóa giá bằng cách redact theo từng word bbox (USD + số tiền),
    không che hình chữ nhật rộng => không đè đường gạch và không xóa label.
    Giữ nguyên kích thước trang PDF.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    for page in doc:
        words = page.get_text("words")
        # words: [x0, y0, x1, y1, "text", block, line, word_no]

        # Gom words theo block+line để xử lý theo dòng
        lines = {}
        for (x0, y0, x1, y1, w, bno, lno, wno) in words:
            key = (bno, lno)
            lines.setdefault(key, []).append((x0, y0, x1, y1, w))

        # Helper: redact đúng bbox của word (nhỏ, không đè line)
        def redact_word_bbox(x0, y0, x1, y1):
            r = fitz.Rect(x0 - margin, y0 - margin, x1 + margin, y1 + margin)
            page.add_redact_annot(r, fill=(1, 1, 1))

        # 1) Xác định mốc cột UNIT PRICE để biết vùng bên phải là giá
        unit_hdr = page.search_for("UNIT PRICE")
        unit_x0 = unit_hdr[0].x0 if unit_hdr else None

        # 2) Xóa USD + số tiền trong vùng cột giá (bên phải UNIT PRICE)
        if unit_x0 is not None:
            for (x0, y0, x1, y1, w, *_rest) in words:
                if x0 >= unit_x0 - 1:  # bên phải header UNIT PRICE
                    if MONEY_TOKEN.match(w):
                        redact_word_bbox(x0, y0, x1, y1)

        # 3) Xóa số tiền ở khu totals: chỉ xóa token tiền bên phải label, giữ lại label
        for lbl in TOTAL_LABELS:
            rects = page.search_for(lbl)
            for rlbl in rects:
                # tìm line gần nhất với y của label
                best_key = None
                best_dy = 10**9
                for (bno, lno), ws in lines.items():
                    ly0 = min(t[1] for t in ws)  # y0 nhỏ nhất của line
                    dy = abs(ly0 - rlbl.y0)
                    if dy < best_dy:
                        best_dy = dy
                        best_key = (bno, lno)

                if best_key is None:
                    continue

                # redact token tiền nằm bên phải label trên line đó
                for (x0, y0, x1, y1, w) in lines[best_key]:
                    if x0 > rlbl.x1 + 3 and MONEY_TOKEN.match(w):
                        redact_word_bbox(x0, y0, x1, y1)

        # 4) Xóa SAY TOTAL: chỉ xóa phần nội dung sau "SAY TOTAL" (thường sau dấu :)
        say_rects = page.search_for("SAY TOTAL")
        for rsay in say_rects:
            best_key = None
            best_dy = 10**9
            for (bno, lno), ws in lines.items():
                ly0 = min(t[1] for t in ws)
                dy = abs(ly0 - rsay.y0)
                if dy < best_dy:
                    best_dy = dy
                    best_key = (bno, lno)

            if best_key is None:
                continue

            for (x0, y0, x1, y1, w) in lines[best_key]:
                if x0 > rsay.x1 + 3:  # phần sau SAY TOTAL
                    redact_word_bbox(x0, y0, x1, y1)

        # Apply redactions (xóa thật nội dung text)
        page.apply_redactions()

    out = io.BytesIO()
    doc.save(out, deflate=True, garbage=4)  # giữ nguyên page size, tối ưu file
    doc.close()
    return out.getvalue()


if uploaded:
    st.subheader("Kết quả")
    for f in uploaded:
        cleaned = redact_like_sample(f.read(), margin=MARGIN)

        # Rename output: (tên file cũ)_tosale.pdf
        base_name = f.name.rsplit(".", 1)[0]
        output_name = f"{base_name}_tosale.pdf"

        st.download_button(
            label=f"Download: {output_name}",
            data=cleaned,
            file_name=output_name,
            mime="application/pdf",
            key=f"dl_{f.name}"
        )
else:
    st.info("Upload PDF invoice")
