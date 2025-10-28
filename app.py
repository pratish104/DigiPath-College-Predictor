import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
import pandas as pd
import json

app = Flask(__name__)
app.secret_key = "digi_secret_key"

# -------------------------
# Load College Data
# -------------------------
csv_path = "data/converted_college_data.csv"
df = pd.read_csv(csv_path)
df.columns = df.columns.str.strip()

# Load College -> City mapping
json_path = "data/college_city_map.json"
with open(json_path, "r") as f:
    college_city_map = json.load(f)

def get_city(college_name):
    return college_city_map.get(college_name, df.loc[df["College Name"] == college_name, "City"].values[0])

df["City"] = df["College Name"].apply(get_city)

# Prepare dropdowns
cities = sorted(df["City"].dropna().unique())
branches = sorted(df["Branch"].dropna().unique())
categories = sorted(df["Category"].dropna().unique())

# -------------------------
# Email entry page
# -------------------------
@app.route("/", methods=["GET", "POST"])
def email_login():
    if request.method == "POST":
        email = request.form.get("email").strip()
        if not email:
            flash("Please enter your email.", "error")
            return redirect(url_for("email_login"))
        session["user"] = email
        flash(f"Welcome, {email}!", "success")
        return redirect(url_for("predict"))
    return render_template("email_login.html")  # simple page with email field

# -------------------------
# Predict Colleges
# -------------------------
@app.route("/predict", methods=["GET", "POST"])
def predict():
    if "user" not in session:
        return redirect(url_for("email_login"))

    colleges = []
    percentage = city = branch = category = ""

    if request.method == "POST":
        try:
            percentage = float(request.form.get("percentage", 0))
        except ValueError:
            percentage = 0

        city = request.form.get("city", "")
        branch = request.form.get("branch", "")
        category = request.form.get("category", "")

        filtered_df = df[df["Cutoff"] <= percentage]
        if city:
            filtered_df = filtered_df[filtered_df["City"] == city]
        if branch:
            filtered_df = filtered_df[filtered_df["Branch"] == branch]
        if category:
            filtered_df = filtered_df[filtered_df["Category"] == category]

        filtered_df = filtered_df.sort_values(by="Cutoff", ascending=False)
        colleges = filtered_df.to_dict(orient="records")

    return render_template(
        "index.html",
        user=session["user"],
        colleges=colleges,
        cities=cities,
        branches=branches,
        categories=categories,
        selected_percentage=percentage,
        selected_city=city,
        selected_branch=branch,
        selected_category=category
    )

# -------------------------
# Logout
# -------------------------
@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("email_login"))

# -------------------------
if __name__ == "__main__":
    from os import environ
    port = int(environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

