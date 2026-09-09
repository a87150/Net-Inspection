# MariaDB migration and Redis page cache

**Goal:** Move the active Demo SQLite data to local MariaDB and cache safe display pages in Redis.

**Architecture:** Django mysql backend with utf8mb4; shared root .env loaded by Web and Worker. Redis is optional page cache only, isolated per session/user/permission and query. Existing SQLite and export remain rollback evidence. No unrelated refactoring or automatic replay of external tasks.

## Execution

- [x] Test environment loading and launcher preserving an explicit MySQL backend. Implement environment loader, update demo so MySQL never seeds demo data. Pin redis and python-dotenv.
- [x] Implement and test isolated short-TTL page middleware in independent files. Bypass sensitive operations, messages, downloads and task polling; degrade on Redis failure.
- [x] Check source task state and local services. Pause only this project's Web/Worker. Back up demo-runtime/demo.sqlite3 and preserve encryption key.
- [x] Create empty net_inspection database, migrate schema, dump all source data with natural foreign keys except generated permissions/contenttypes, import, compare model counts and normalized record hashes.
- [x] Write ignored .env for shared database/cache configuration, verify Django checks and representative read pages against MariaDB. Do not launch live directory/inspection tests.
- [x] Restart Web and Worker with same environment, verify HTTP/cache hit and task infrastructure safely. Record rollback/start instructions in README.

## Safety

Never clear SQLite, overwrite an existing MariaDB application database, flush shared Redis, or print credentials. Migration may stop for an active external task rather than aborting it. Backups contain personal data and stay ignored locally. Do not commit shared dirty worktree.
