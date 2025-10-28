import pandas as pd
import os
import json

# Load CAP dataset
input_file = "data/CAP_Cutoff_Data.csv"
df = pd.read_csv(input_file)
df.columns = df.columns.str.strip()

# Load college-city mapping
with open("data/college_city_map.json", "r") as f:
    college_city_map = json.load(f)

# Map cities
df["City"] = df["College Name"].apply(lambda x: college_city_map.get(x, "Others"))

# Create simplified columns
df["Branch"] = df["Course Name"]
df["Category"] = df["Category"]
df["Cutoff"] = df["Percent"] if "Percent" in df.columns else df["Cutoff Rank"]

# Select final columns
df_final = df[["College Name", "City", "Branch", "Category", "Cutoff"]]

# Save converted dataset
output_file = "data/converted_college_data.csv"
df_final.to_csv(output_file, index=False)

print(f"✅ Conversion completed! Saved as {output_file}")
print(df_final.head(10))
