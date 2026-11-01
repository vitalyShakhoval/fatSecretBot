from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def main() -> None:
    path = Path(os.getenv("DATABASE_PATH", "health_bot.sqlite3"))
    if path.exists():
        with sqlite3.connect(path) as db:
            db.execute("SELECT 1")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not os.access(path.parent, os.W_OK):
            raise SystemExit("Database directory is not writable")
    print("ok")


if __name__ == "__main__":
    main()
