import io
import streamlit as st
import fitz  # PyMuPDF

st.set_page_config(page_title="Invoice - Remove Price", layout="wide")
st.title("Invoice PDF - Xóa giá")

uploaded = st.file_uploader("Upload invoice PDF", type=["pdf"], accept_multiple_files=True)

MARGIN = st.slider("Lề an toàn (px)", 0, 30, 6)

def redact_like_sample(pdf_bytes: bytes, margin: int = 6) -> bytes:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    for page in doc:
        pr = page.rect  # giữ nguyên size trang

        # 1) Che vùng giá của dòng hàng: từ cột UNIT PRICE sang phải, nhưng chỉ phần body (không che header)
        unit_hdr = page.search_for("UNIT PRICE")
        if unit_hdr:
            u = unit_hdr[0]
            x0 = max(0, u.x0 - margin)

            # body price area: lấy vị trí "USD" trong vùng bảng (khoảng y 250-320), rồi che đúng dải đó
            usd_rects = page.search_for("USD")
            body_usd = [r for r in usd_rects if 250 < r.y0 < 320]
            if body_usd:
                y0 = min(r.y0 for r in body_usd) - 6
                y1 = max(r.y1 for r in body_usd) + 6
            else:
                y0 = u.y1 + 8
                y1 = pr.height * 0.6

            # chặn đến trước phần totals nếu tìm thấy
            sub = page.search_for("SUB TOTAL AMOUNT")
            if sub:
                y1 = min(y1, sub[0].y0 - 4)

            page.add_redact_annot(fitz.Rect(x0, y0, pr.width, y1), fill=(1, 1, 1))

        # 2) Che box totals bên phải (SUB TOTAL / TAX / TOTAL… + số tiền)
        sub = page.search_for("SUB TOTAL AMOUNT")
        total_amount = page.search_for("TOTAL AMOUNT")
        if sub and total_amount:
            y0 = sub[0].y0 - 6
            y1 = total_amount[-1].y1 + 6

            usd_rects = page.search_for("USD")
            totals_usd = [r for r in usd_rects if r.y0 >= y0 - 2 and r.y1 <= y1 + 2]
            x0 = (min(r.x0 for r in totals_usd) - 10) if totals_usd else pr.width * 0.75

            page.add_redact_annot(fitz.Rect(max(0, x0), y0, pr.width, y1), fill=(1, 1, 1))

        # 3) Che dòng SAY TOTAL (phần chữ tiền) – che từ sau chữ "SAY TOTAL" tới hết dòng
        say = page.search_for("SAY TOTAL")
        if say:
            r = say[0]
            page.add_redact_annot(
                fitz.Rect(r.x1 + 4, r.y0 - 4, pr.width, r.y1 + 10),
                fill=(1, 1, 1)
            )

        page.apply_redactions()

    out = io.BytesIO()
    doc.save(out, deflate=True, garbage=4)  # giữ nguyên page size, tối ưu file
    doc.close()
    return out.getvalue()

if uploaded:
    for f in uploaded:
        cleaned = redact_like_sample(f.read(), margin=MARGIN)

        # Lấy tên file gốc (bỏ .pdf)
        base_name = f.name.rsplit(".", 1)[0]

        # Tên file output theo yêu cầu
        output_name = f"{base_name}_tosale.pdf"

        st.download_button(
            label=f"Download: {output_name}",
            data=cleaned,
            file_name=output_name,
            mime="application/pdf",
            key=f.name
        )

