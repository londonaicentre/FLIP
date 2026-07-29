"""
Add tabular data from csv files to the tables in the OMOP database.
"""

import argparse
import os

import pandas as pd
from config import get_settings
from sqlalchemy import create_engine, text

# Parse command-line argument
parser = argparse.ArgumentParser(description="Load CSV data into OMOP database.")
parser.add_argument("trust", choices=["trust_1", "trust_2"], help="Specify the trust source.")
parser.add_argument("projects", nargs="+", help="List of project names to load (e.g., spleen_project cxr_project).")
args = parser.parse_args()

# Create sync engine for pandas
engine = create_engine(get_settings().OMOP_DATABASE_URL.get_secret_value(), echo=False)

# Define tables and clean them first
tables = [
    "person",
    "procedure_occurrence",
    "visit_occurrence",
    "image_occurrence",
    "image_feature",
    "measurement",
    "observation",
]

optional_tables = [
    "measurement",
    "observation",
]

with engine.begin() as conn:
    for table_name in tables:
        print(f"🧹 Cleaning table: {table_name}")
        conn.execute(text(f"DELETE FROM omop.{table_name};"))
print("✅ All target tables cleaned.\n")

# Load CSV data for each project
for project in args.projects:
    data_path = os.path.join("data", args.trust, project)
    print(f"📦 Loading data for {args.trust} / {project}")
    for table_name in tables:
        # Load CSV file into a Pandas DataFrame
        csv_file_path = os.path.join(data_path, f"{table_name}.csv")
        if not os.path.isfile(csv_file_path) and table_name in optional_tables:
            print(f"⚠️  Optional table CSV not found, skipping: {csv_file_path}")
            continue

        print(f"Loading data from: {csv_file_path}")
        df = pd.read_csv(csv_file_path)

        # Write DataFrame to SQL database
        df.to_sql(table_name, engine, if_exists="append", index=False, schema="omop")
        print(f"✅ Inserted {len(df)} rows into omop.{table_name}")
    print(" ")

# Print total number of rows in the first table as a sanity check
with engine.begin() as conn:
    result = conn.execute(text(f"SELECT COUNT(*) FROM omop.{tables[0]};"))
    total_rows = result.scalar()
    print(f"\nTotal rows in omop.{tables[0]} (sanity check): {total_rows}")

print("\n🎉 Finished populating OMOP database for", args.trust)
