import pdfplumber
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

# FE ke liye separate output name
output_file = "data/fe_raw_data.csv"
df.to_csv(output_file, index=False)

print(f"FE CSV created → {output_file}")
