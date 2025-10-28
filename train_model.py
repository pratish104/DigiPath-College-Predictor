import pandas as pd
from sklearn.tree import DecisionTreeClassifier
import pickle

# Load converted dataset
df = pd.read_csv("data/converted_college_data.csv")
df.columns = df.columns.str.strip()

# Encode categorical features for the model
df["City_code"] = df["City"].astype("category").cat.codes
df["Branch_code"] = df["Branch"].astype("category").cat.codes
df["Category_code"] = df["Category"].astype("category").cat.codes

# Save the encoders for later use in app.py
encodings = {
    "City": dict(enumerate(df["City"].astype("category").cat.categories)),
    "Branch": dict(enumerate(df["Branch"].astype("category").cat.categories)),
    "Category": dict(enumerate(df["Category"].astype("category").cat.categories))
}

# Features & Labels
X = df[["Cutoff", "City_code", "Branch_code", "Category_code"]]
y = df["College Name"]

# Train Decision Tree
model = DecisionTreeClassifier(random_state=42)
model.fit(X, y)

# Save model
with open("models/model.pkl", "wb") as f:
    pickle.dump(model, f)

# Save encodings
with open("models/encodings.pkl", "wb") as f:
    pickle.dump(encodings, f)

print("✅ Model trained and saved -> models/model.pkl")
print("✅ Encodings saved -> models/encodings.pkl")
