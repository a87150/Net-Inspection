# Backend maintenance

These operations keep the existing DB_ENGINE / DJANGO_SQLITE_PATH selection and
startup entry points. No model or migration changes are introduced here. Commands
use the already configured default database. Nothing runs automatically.

## SQLite contention

`NET_SQLITE_TIMEOUT` sets SQLite's connection busy timeout in seconds: default 5,
finite and greater than 0, maximum 60. Invalid values fail settings loading for
SQLite; MySQL options are unchanged. Restart both Web and Worker after changing
this environment variable so new connections use it. A longer timeout tolerates
short write contention; it does not create concurrent SQLite writers.

WAL is an optional, explicit maintenance operation, never an AppConfig startup
hook. Merely setting `NET_SQLITE_WAL_ENABLED=true` does not execute any PRAGMA.
During a maintenance window, stop Web and all Workers, back up the database, then:

```powershell
.venv/Scripts/python.exe manage.py sqlite_wal
$env:NET_SQLITE_WAL_ENABLED = 'true'
.venv/Scripts/python.exe manage.py sqlite_wal --apply
```

The first command only reads journal mode. The second requires the opt-in and
enables WAL only for an existing ordinary file-backed SQLite database. Memory
databases, SQLite URI filenames, non-SQLite backends, and test/testserver/pytest
processes are skipped. Missing files and transactions are refused. The command
checks SQLite actually accepted WAL, leaves synchronous settings intact, and
does not disable WAL on later starts. Setting the environment flag back to false
only revokes this command's permission; journal mode persists in the database.
Use WAL on a local filesystem shared by processes on the same host, not an SMB/NFS
database file. Preserve associated WAL files when taking backups of a running DB;
prefer a supported SQLite backup or a fully stopped service. Restart services
after maintenance. This implementation has not changed any live database.

## Historical result retention

Historical large snapshots remain until an operator optionally runs maintenance.
Default behavior is read-only preview; even `--apply` alone only creates an archive.
No command deletes evidence rows, audit history, PROTECT links, log files, or tasks.
No automatic cleanup, VACUUM, or archive expiry is installed.

Use an explicit past ISO timestamp with timezone and a bounded limit (1..1000).
Both task and target must be terminal and finished strictly before the cutoff.
Tasks with any nonterminal target are excluded. Missing completion timestamps and
active tasks are excluded. Results are ordered by completion time and UUID.

```powershell
# Preview candidate IDs and incoming PROTECT/RESTRICT reference counts.
.venv/Scripts/python.exe manage.py archive_task_results --before '2026-08-01T00:00:00+08:00' --limit 100

# Also assess which duplicated details can safely be compacted (still read-only).
.venv/Scripts/python.exe manage.py archive_task_results --before '2026-08-01T00:00:00+08:00' --limit 100 --compact

# Create an archive only. The parent directory must exist; the file must not.
.venv/Scripts/python.exe manage.py archive_task_results --before '2026-08-01T00:00:00+08:00' --limit 100 --apply --output 'D:/Backups/net/results-001.jsonl'

# Optional: durable archive first, then compact verified duplicates.
.venv/Scripts/python.exe manage.py archive_task_results --before '2026-08-01T00:00:00+08:00' --limit 100 --apply --compact --output 'D:/Backups/net/results-compact-001.jsonl'
```

Each summary supplies `next_cursor` with `after_id` and `after_finished_at`. Pass
these as `--after-id` and `--after-finished-at` with the same cutoff to continue in
a NEW output file. Continue until count is zero. Preview does not reserve rows;
apply re-evaluates current eligibility. Retain per-batch cursors and archives.

Plain preview omits JSON payloads. Compaction preview loads evidence to validate
it. Reference counts explain direct deletion blockers; they are not authorization
to delete anything or a complete recursive deletion analysis. A null
`compaction_blocked` means duplicate details are eligible at preview time.

Archives are UTF-8 JSONL: a versioned manifest, complete target and task concrete
fields (including original snapshots and result references), fetched-log IDs and
incoming protected reference counts, then a `complete` footer with count and
SHA-256 of all preceding raw bytes, including newlines. This is a snapshot archive,
not a complete database backup; referenced records/files stay in the database and
storage. Check the footer count, hash, and command success before relying on it.
Exclusive file creation refuses overwrite. Payload and footer are separately
flushed and fsynced before any database update. Secure the archive directory with
Windows ACLs (POSIX creation mode is 0600): exports contain historical operational
and potentially personal data. Storage must honor flush/fsync durability.

`--apply --compact` uses the v2 result-storage helper only when the supported
record exists, references this exact target, and its details exactly equal the
old snapshot's details. Missing/mismatched references, unknown formats, existing
v2 snapshots, and changes that would not save bytes are skipped. Only duplicate
`details` is removed; all original status, health, summary and other markers are
preserved, including the absence of status/health keys in legacy snapshots; helper
defaults never introduce new outcome markers. References/version are supplied by
the helper. PC `exceptions` and `analysis_items` are retained unchanged, even when
they duplicate the canonical record; this command removes only matched `details`.
Evidence is never
rewritten. Before each update, the command locks/rechecks the record in a short
transaction and compares the current snapshot, references, task/target terminal
state, and cutoff. The database update uses compare-and-swap, skipping concurrent
changes instead of overwriting them. This intentionally bypasses the ordinary
model-save immutability guard only for the explicitly requested maintenance.

Run compaction in a maintenance window with Web/Workers stopped. Archives are
synced in full before per-target transactions begin; an interrupted compaction
can leave a partially compacted batch, with the complete original archive retained.
The footer certifies archive completion, not compaction completion; inspect the
command summary for `compacted`. On any error keep the archive and reconcile with
the database; retry using a new path. Incomplete archives must never be used as
proof of backup. There is no automatic restore/import command. Restoring original
snapshots requires a separately reviewed operation using the archive and current
record identities; do not overwrite concurrent data. Compaction can reduce JSON
storage but does not necessarily shrink the SQLite file on disk.

## Verification

Focused tests run with an isolated Django test database and temporary SQLite files:

```powershell
$env:DB_ENGINE = 'sqlite'
$env:NET_SQLITE_WAL_ENABLED = 'false'
.venv/Scripts/python.exe manage.py test tests.system.test_backend_maintenance --noinput
```

WAL is tested only on disposable files, never the configured service database.
