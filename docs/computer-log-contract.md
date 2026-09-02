# Computer log import and selected analysis contract

The Worker reads only JSON files already present in configured server-side
directories. It does not start PowerShell, contact computers, or open remote
terminals. Analysis of an existing `ComputerLogFile` never rescans its source.

## Selected fields

Only selected items generate details/findings. An absent required top-level key
produces `data_state=missing`; null, a wrong shape, unrecognized status or invalid
required nested value produces `data_state=unknown`. Both are failed analyses,
with the selected item named in `analysis_item`. Invalid selected details retain
their original (sanitized) shape, not a fabricated empty list or healthy status.

| Item | Required PowerShell JSON contract | Empty / normal semantics |
| --- | --- | --- |
| activation | `Windows激活信息` object with known `许可证状态` text | `已授权` is normal; other known states are findings. KMS clients are checked against the configured server list when that list is nonempty. |
| software | `已安装软件列表` array; each object has nonempty known `软件名` | `[]` means collected, no entries. When a policy path is configured, INI allow-list, employee-specific allow-list and deny-list rules are applied. |
| processes | `当前运行进程清单` array; each object has nonempty known `进程名` | `[]` means collected, no entries; inventory only. |
| bitlocker | `BitLocker状态.磁盘卷信息` array; each row has known `卷` and `转换状态` | Empty volume array is explicitly `data_state=empty` and fails because it cannot establish encryption. Fully/encrypting used-space states use the existing encryption rule. |
| defender | `WindowsDefender状态` object with known nonempty `当前病毒库版本` and valid update/scan times | Empty fields/object are unknown. Update and scan ages use their configured maximum days. |
| patches | `系统更新历史` array; rows have known `补丁名称` and valid `日期` | `[]` means collected, no history entries. The newest patch date is checked against the configured maximum age. |
| domain | `已应用策略` object and `当前与域服务器通讯情况` | Empty policy object is valid if communication is explicitly `正常通讯` or `未加入域`; `无法访问` is an issue; other statuses are unknown. |
| system | `系统信息概览` object with `系统主要版本名` | The Windows release is compared with the configured minimum release. |
| uptime | `系统信息概览` object with `开机时间` | Elapsed uptime is compared with the configured maximum hours. |
| resource | `计算机硬件资源情况` object with `当前CPU占用率` and `当前内存使用率` | Both must be percentage strings in 0–100 and are checked against their configured warning thresholds. |
| event_findings | `事件发现` array (legacy `事件日志` only if primary key is absent); rows have known `级别`, `severity` or `Level` | `[]` means no events; info/information/informational, warning, error, critical, verbose and documented Chinese equivalents are recognized. Error/critical are findings. |

Dates accept `YYYY-MM-DD HH:MM:SS` or `YYYY-MM-DD`. The existing supplied
`agents/pc/windows/GetInfo_JSON.ps1` does **not** collect processes, domain or events: selecting
those options on its output explicitly fails with missing-data findings. The
script is unchanged by the review fix. Singleton objects instead of arrays and
null collection outputs are not silently treated as valid collections.

`success` means the selected data satisfied these contracts and no implemented
rule found an issue. It is not a broader security/compliance certification.

## Configurable analysis rules

The log-analysis configuration dialog stores the software-policy INI path,
minimum Windows release, Defender update/scan ages, patch age, maximum uptime,
CPU/memory thresholds and allowed KMS servers. Relative policy paths resolve
from the Django project root. A missing or malformed policy is saved as a
`软件策略问题` finding instead of aborting the Worker. Domain-qualified employee
identities such as `DOMAIN\\H1` are matched by employee number (`H1`).

Each queued task contains an immutable snapshot of these values, so editing the
profile does not change rules for a task that is already waiting or running.

## File boundaries and producer handoff

Each poll snapshots its configuration. Recent N days is the inclusive rolling
window `[now - N days, now]`; explicit dates cover both complete days in Django's
current/configured timezone, `[start midnight, midnight after end date)`.
Recursion is optional. Configured processed/failed directories must be below a
scan root and are excluded from discovery. Linked files, ancestor symlinks and
Windows directory junctions are rejected.

The scanner checks device/inode identity, size and nanosecond mtime before and
after reading a regular-file descriptor. Total bytes read are bounded at
`MAX_LOG_FILE_BYTES + 1` (default limit 16 MiB). Unstable files stay at source and
produce retryable summaries. A SHA-256 check of the exact bytes and identity is
repeated before archive and checked after rename. In-place changes with unchanged
size/mtime are therefore detected as well. Malformed, nonfinite or excessively
nested JSON is persisted as failed evidence; the original file is preserved.

Producers should write a temporary non-JSON name, close it, then atomically rename
it into the scan folder. They must not keep writing through an open descriptor
after handoff. No portable scanner can prevent an uncooperative producer holding
an already-open file from modifying it after the scanner has finished its checks.

## Transactions and recoverable archives

Import/scanning entry points reject an enclosing application transaction. Each
hash attempt uses its own durable transaction; duplicate insert/deadlock/lock-timeout
races roll back the whole transaction before retrying, at most three times. Valid,
malformed and static-schema-invalid files use the same path. Static asset updates
and evidence insert commit together. Exhausted races are summarized per candidate
and do not abort the remaining poll. SQLite writers are additionally serialized
inside a process; production MySQL uses row locks, uniqueness and fresh retries.

Every source copy (including hash duplicates) has a `ComputerLogArchive` journal:
a deterministic key/path derived from source path, identity and content hash is
committed as `pending` **before** the filesystem move. Windows uses no-replace
`rename`; Linux uses `renameat2(RENAME_NOREPLACE)`. There is no overwrite or
copy/delete fallback: cross-filesystem/unsupported moves fail safely and retain
the source plus journal. Configure archive folders on the same filesystem.

Completing the journal and updating the legacy evidence `archived_path` share a
transaction. If that transaction fails after a successful move, the next scan
reconciles the durable destination by identity/hash even when source is absent.
A failed move retries the same pending path. Changed generations are restored
to source; if a new upload already owns that name, recovery uses a deterministic
`*-retry.json` sibling without overwriting either file. Old attempts become
`changed`. Destination collisions are retained and reported, never overwritten.
Pending paths outside the currently configured roots are not followed; restore
the original permitted configuration to reconcile them.

Analysis history, Worker lease/cancellation checks and recursive sanitization
remain unchanged. The additive archive-journal migration contains no data migration.
