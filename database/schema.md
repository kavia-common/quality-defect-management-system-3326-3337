# Quality Defect Management System — Canonical SQLite Schema

This folder’s canonical SQLite DB file is:

- `database/myapp.db`

The Django backend is expected to point at this exact file (or an override via env var) to avoid split data.

## Tables

### 1) `workflow_statuses`
Defines the workflow states a defect can be in.

Columns:
- `id` (PK)
- `code` (UNIQUE) — e.g. `NEW`, `TRIAGED`, `IN_PROGRESS`, `CLOSED`
- `name`
- `description`
- `sort_order` (int)
- `is_terminal` (0/1)
- `is_active` (0/1)
- `created_at`
- `updated_at`

### 2) `defects`
Core defect record.

Columns:
- `id` (PK)
- `defect_key` (UNIQUE, optional) — external-friendly identifier
- `title` (required)
- `description`
- `severity` — app-enforced enum (`low|medium|high|critical`)
- `priority` — app-enforced enum (`low|medium|high|urgent`)
- `status_id` (FK → `workflow_statuses.id`)
- `reported_by` (text)
- `assigned_to` (text)
- `area` (text)
- `source` (text)
- `occurred_at` (timestamp)
- `due_date` (timestamp)
- `closed_at` (timestamp)
- `created_at`
- `updated_at`

Indexes:
- `idx_defects_status_id`
- `idx_defects_due_date`
- `idx_defects_created_at`

### 3) `five_why_analyses`
5-Why analysis and root cause (1:1 with defect).

Columns:
- `id` (PK)
- `defect_id` (UNIQUE, FK → `defects.id`, ON DELETE CASCADE)
- `problem_statement`
- `why1` ... `why5`
- `root_cause`
- `created_by`
- `created_at`
- `updated_at`

Index:
- `idx_five_why_defect_id`

### 4) `corrective_actions`
Corrective and preventive actions (CAPA-style) (1:N with defect).

Columns:
- `id` (PK)
- `defect_id` (FK → `defects.id`, ON DELETE CASCADE)
- `title` (required)
- `description`
- `owner`
- `due_date`
- `completed_at`
- `status` — app-enforced enum (`open|in_progress|blocked|done|cancelled`)
- `effectiveness_check`
- `created_at`
- `updated_at`

Indexes:
- `idx_actions_defect_id`
- `idx_actions_due_date`

### 5) `defect_history`
Append-only audit trail and comments.

Use cases:
- comments
- status changes (store `from_status_id` and `to_status_id`)
- edits / updates / system events

Columns:
- `id` (PK)
- `defect_id` (FK → `defects.id`, ON DELETE CASCADE)
- `event_type` — `comment|status_change|edit|analysis_update|action_update|system`
- `message`
- `from_status_id` (nullable FK → `workflow_statuses.id`)
- `to_status_id` (nullable FK → `workflow_statuses.id`)
- `actor`
- `created_at`

Indexes:
- `idx_history_defect_id`
- `idx_history_created_at`

### 6) `app_info`
Small key/value table used for tooling/version metadata.

Columns:
- `id` (PK)
- `key` (UNIQUE)
- `value`
- `created_at`

## Notes on integration approach

- **Single source of truth DB file**: `database/myapp.db`
- Django backend is configured to use `../database/myapp.db` by default, with optional env override:
  - `SQLITE_DB_PATH=/absolute/or/relative/path/to/myapp.db`

If you change the DB location, also update:
- `database/db_connection.txt`
- `database/db_visualizer/sqlite.env`
