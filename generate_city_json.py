import pandas as pd
import re
import json
import os

# Path to your CSV
csv_path = "data/CAP_Cutoff_Data.csv"

# Check if CSV exists
if not os.path.exists(csv_path):
    print(f"❌ CSV not found at {csv_path}")
    exit()

# Load CSV
df = pd.read_csv(csv_path)
df.columns = df.columns.str.strip()

# Function to extract city from college name
def extract_city(college_name):
    try:
        # Take last part after comma
        city = college_name.split(",")[-1]
        # Remove brackets and extra spaces
        city = re.sub(r"\(.*\)", "", city).strip()
        if city == "":
            city = "Others"
        return city
    except:
        return "Others"

# Apply function
df["City"] = df["College Name"].apply(extract_city)

# Create JSON mapping
college_city_map = dict(zip(df["College Name"], df["City"]))

# Save JSON
json_path = "data/college_city_map.json"
with open(json_path, "w") as f:
    json.dump(college_city_map, f, indent=4)

print(f"✅ JSON mapping created successfully at {json_path}")
