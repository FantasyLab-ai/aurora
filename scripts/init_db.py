# scripts/init_db.py
from fantasyai.db.storage import init_schema

if __name__ == "__main__":
    init_schema()
    print("FantasyAI core DB schema initialized.")
