import os, sqlite3, pathlib

# Resolve BASE_DIR from env or default to Windows Desktop path via WSL
BASE_DIR = os.path.expanduser(os.getenv("BASE_DIR", "/mnt/c/Users/bgrut/Desktop/FantasyAI"))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH  = os.path.join(DATA_DIR, "fantasyai.db")

# Candidate schema locations (first existing wins)
CANDIDATES = [
    os.getenv("SCHEMA_PATH", ""),                          # explicit override
    os.path.join(BASE_DIR, "schema.sql"),                  # project root
    "/mnt/c/Users/bgrut/Desktop/FantasyAI/schema.sql",     # common absolute
]

def _find_schema():
    for p in CANDIDATES:
        if p and os.path.exists(p):
            return p
    raise FileNotFoundError(f"schema.sql not found. Tried: {CANDIDATES}")

os.makedirs(DATA_DIR, exist_ok=True)

def ensure_schema(db_path: str = DB_PATH, schema_path: str | None = None):
    schema_path = schema_path or _find_schema()
    sql = pathlib.Path(schema_path).read_text(encoding="utf-8")
    con = sqlite3.connect(db_path)
    try:
        con.executescript(sql)
        con.commit()
    finally:
        con.close()
    return db_path
