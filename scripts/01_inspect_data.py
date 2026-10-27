import pandas as pd

DATA_PATH = "data/raw/twcs.csv"

print("Loading dataset...")

df = pd.read_csv(DATA_PATH)

print("\n=== DATASET SHAPE ===")
print(df.shape)

print("\n=== COLUMNS ===")
print(df.columns.tolist())

print("\n=== FIRST 5 ROWS ===")
print(df.head())

print("\n=== DATA TYPES ===")
print(df.dtypes)

print("\n=== MISSING VALUES ===")
print(df.isnull().sum())

print("\n=== AUTHOR TYPE COUNTS ===")
print(df["inbound"].value_counts())

print("\n=== UNIQUE AUTHORS ===")
print(df["author_id"].nunique())

print("\n=== TOP OUTBOUND AUTHORS ===")

outbound = df[df["inbound"] == False]

top_authors = (
    outbound["author_id"]
    .value_counts()
    .head(30)
)

print(top_authors)