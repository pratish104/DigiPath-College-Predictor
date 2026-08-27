import pdfplumber # type: ignore
import pandas as pd

rows = []

pdf_path = "data/fe2025.mahacet.org.pdf"

with pdfplumber.open(pdf_path) as pdf:
    for page in pdf.pages[:20]:   # demo ke liye 20 pages
        text = page.extract_text()
        if text:
            lines = text.split("\n")
            for line in lines:
                rows.append([line])

df = pd.DataFrame(rows, columns=["RawText"])

df.to_csv("data/pdf_raw_output.csv", index=False)

print("PDF converted to CSV successfully!")
