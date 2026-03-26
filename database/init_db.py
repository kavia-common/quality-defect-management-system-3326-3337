#!/usr/bin/env python3
"""Initialize SQLite database for the Quality Defect Management System.

This script creates (or migrates) the canonical schema used by the Django backend
and database utilities in this repo.

Design goals:
- Stable DB filename: myapp.db (in this directory)
- Foreign keys enabled
- Canonical schema for:
  - workflow statuses
  - defects
  - 5-Why analysis
  - corrective actions
  - comments/history (audit trail)
- Idempotent: safe to run multiple times

Note:
- This script intentionally uses SQLite DDL directly rather than Django migrations
  because it is meant for the *database* container utilities and canonical schema
  documentation. The Django backend points to the same DB file.
"""

import os
import sqlite3
from pathlib import Path

DB_NAME = "myapp.db"

# These are not used for SQLite, but kept for consistency with template scripts.
DB_USER = "kaviasqlite"
DB_PASSWORD = "kaviadefaultpassword"
DB_PORT = "5000"


def _connect(db_path: Path) -> sqlite3.Connection:
    """Create a sqlite3 connection with recommended pragmas."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Enable foreign keys (off by default in SQLite)
    conn.execute("PRAGMA foreign_keys = ON;")
    # Better concurrency characteristics (still single-writer, but improves readers)
    conn.execute("PRAGMA journal_mode = WAL;")
    # Reasonable durability; adjust if you need strictest durability.
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    )
    return cur.fetchone() is not None


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create canonical tables and indexes (idempotent)."""
    cur = conn.cursor()

    # --- Reference / workflow tables ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_statuses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,              -- e.g., NEW, TRIAGED, IN_PROGRESS, VERIFIED, CLOSED
            name TEXT NOT NULL,                     -- human readable label
            description TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,  -- for UI ordering
            is_terminal INTEGER NOT NULL DEFAULT 0, -- 1 when status indicates completion/closure
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
        );
        """
    )

    # --- Core defect record ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS defects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            defect_key TEXT UNIQUE,                         -- optional external-friendly key (e.g., DEF-000123)
            title TEXT NOT NULL,
            description TEXT,
            severity TEXT NOT NULL DEFAULT 'medium',        -- low|medium|high|critical (enforced in app layer)
            priority TEXT NOT NULL DEFAULT 'medium',        -- low|medium|high|urgent (enforced in app layer)
            status_id INTEGER NOT NULL,
            reported_by TEXT,                               -- free text (could later become FK to auth user)
            assigned_to TEXT,                               -- free text
            area TEXT,                                      -- product/process area
            source TEXT,                                    -- audit, customer, internal, supplier, etc.
            occurred_at TIMESTAMP,                          -- when defect occurred
            due_date TIMESTAMP,                             -- used for overdue alerts
            closed_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP,
            FOREIGN KEY (status_id) REFERENCES workflow_statuses(id)
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_defects_status_id ON defects(status_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_defects_due_date ON defects(due_date);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_defects_created_at ON defects(created_at);")

    # --- 5-Why root cause analysis (one per defect) ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS five_why_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            defect_id INTEGER NOT NULL UNIQUE,
            problem_statement TEXT,          -- optional restatement of problem
            why1 TEXT,
            why2 TEXT,
            why3 TEXT,
            why4 TEXT,
            why5 TEXT,
            root_cause TEXT,
            created_by TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP,
            FOREIGN KEY (defect_id) REFERENCES defects(id) ON DELETE CASCADE
        );
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_five_why_defect_id ON five_why_analyses(defect_id);"
    )

    # --- Corrective actions (many per defect) ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS corrective_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            defect_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            owner TEXT,
            due_date TIMESTAMP,
            completed_at TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'open',   -- open|in_progress|blocked|done|cancelled (app layer)
            effectiveness_check TEXT,              -- notes for verification
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP,
            FOREIGN KEY (defect_id) REFERENCES defects(id) ON DELETE CASCADE
        );
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_actions_defect_id ON corrective_actions(defect_id);"
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_actions_due_date ON corrective_actions(due_date);")

    # --- History/comments / audit trail ---
    # Stores comments as well as key events (status changes, edits) as an append-only log.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS defect_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            defect_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,                 -- comment|status_change|edit|analysis_update|action_update|system
            message TEXT,                             -- human-readable description
            from_status_id INTEGER,
            to_status_id INTEGER,
            actor TEXT,                               -- user identifier or free text
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (defect_id) REFERENCES defects(id) ON DELETE CASCADE,
            FOREIGN KEY (from_status_id) REFERENCES workflow_statuses(id),
            FOREIGN KEY (to_status_id) REFERENCES workflow_statuses(id)
        );
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_defect_id ON defect_history(defect_id);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_created_at ON defect_history(created_at);"
    )

    # Optional compatibility: keep a tiny app_info table for tooling/versioning.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS app_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    conn.commit()


def _seed_workflow_statuses(conn: sqlite3.Connection) -> None:
    """Insert default workflow statuses if none exist."""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM workflow_statuses;")
    count = cur.fetchone()["c"]
    if count > 0:
        return

    default_statuses = [
        ("NEW", "New", "Reported; awaiting triage", 10, 0),
        ("TRIAGED", "Triaged", "Reviewed and categorized; next steps assigned", 20, 0),
        ("IN_PROGRESS", "In Progress", "Investigation or remediation in progress", 30, 0),
        ("PENDING_VERIFICATION", "Pending Verification", "Fix/actions completed; awaiting verification", 40, 0),
        ("VERIFIED", "Verified", "Verified effective; ready to close", 50, 0),
        ("CLOSED", "Closed", "Closed/completed", 60, 1),
    ]
    cur.executemany(
        """
        INSERT INTO workflow_statuses (code, name, description, sort_order, is_terminal)
        VALUES (?, ?, ?, ?, ?);
        """,
        default_statuses,
    )
    conn.commit()


def main() -> None:
    """Create/validate the canonical SQLite DB and write connection documentation."""
    db_path = Path(__file__).resolve().parent / DB_NAME
    print("Starting SQLite setup...")
    print(f"Target DB file: {db_path}")

    db_exists = db_path.exists()
    if db_exists:
        print("SQLite database already exists - will ensure schema is up to date.")
    else:
        print("Creating new SQLite database...")

    conn = _connect(db_path)
    try:
        _ensure_schema(conn)
        _seed_workflow_statuses(conn)

        # Basic stats
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        table_count = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) AS c FROM workflow_statuses;")
        status_count = cur.fetchone()["c"]

        print("Schema ensured.")
        print(f"Tables: {table_count}")
        print(f"Workflow statuses: {status_count}")
    finally:
        conn.close()

    # Write db_connection.txt (canonical path reference)
    connection_string = f"sqlite:///{db_path}"
    try:
        with open(Path(__file__).resolve().parent / "db_connection.txt", "w", encoding="utf-8") as f:
            f.write("# SQLite connection methods (canonical for Quality Defect Management System):\n")
            f.write(f"# Python: sqlite3.connect('{db_path}')\n")
            f.write(f"# Connection string: {connection_string}\n")
            f.write(f"# File path: {db_path}\n")
        print("Connection information saved to db_connection.txt")
    except Exception as e:
        print(f"Warning: Could not save connection info: {e}")

    # Write Node.js viewer env file
    try:
        visualizer_dir = Path(__file__).resolve().parent / "db_visualizer"
        visualizer_dir.mkdir(parents=True, exist_ok=True)
        with open(visualizer_dir / "sqlite.env", "w", encoding="utf-8") as f:
            f.write(f'export SQLITE_DB="{db_path}"\n')
        print("Environment variables saved to db_visualizer/sqlite.env")
    except Exception as e:
        print(f"Warning: Could not save environment variables: {e}")

    print("\nSQLite setup complete!")
    print(f"Database: {DB_NAME}")
    print(f"Location: {db_path}")
    print("\nScript completed successfully.")


if __name__ == "__main__":
    main()
