# SQL Review Rules

## All Versions (version-agnostic)

### Security

- [ ] No dynamic SQL constructed via string concatenation with user input (SQL injection)
- [ ] Stored procedures accepting user input use parameterized queries, not `EXEC('... ' + @param)`
- [ ] Database users have minimum required privileges — no `GRANT ALL` to application users
- [ ] Sensitive columns (passwords, SSN, PII) encrypted at rest or stored as hashes
- [ ] Views used to expose only required columns to application users — not full table access

### Migration Safety

- [ ] Columns are NOT dropped in the same migration that removes code references — two-step deploy
- [ ] `NOT NULL` columns added with a `DEFAULT` value or in two steps (add nullable, backfill, add constraint)
- [ ] Indexes created `CONCURRENTLY` (PostgreSQL) or with `ONLINE = ON` (SQL Server) on large tables
- [ ] Destructive operations (`DROP TABLE`, `TRUNCATE`) protected by an explicit check or run in a transaction
- [ ] Migrations are idempotent where possible — check existence before creating (`IF NOT EXISTS`)
- [ ] Rollback plan documented for every migration that removes or restructures data

### Query Correctness

- [ ] `SELECT *` not used in production queries — columns listed explicitly
- [ ] `JOIN` type explicitly stated (`INNER`, `LEFT`, `RIGHT`) — no implicit cross joins via comma syntax
- [ ] `NULL` comparisons use `IS NULL` / `IS NOT NULL`, not `= NULL` / `<> NULL`
- [ ] Aggregates with `GROUP BY` include all non-aggregated `SELECT` columns (ANSI compliance)
- [ ] `HAVING` used for aggregate conditions — not `WHERE` after `GROUP BY`
- [ ] Correlated subqueries in `SELECT` or `WHERE` evaluated for N+1 performance implications

### Performance

- [ ] Queries on large tables filtered on indexed columns — full table scans flagged
- [ ] `LIKE '%prefix'` patterns that cannot use an index noted as a performance concern
- [ ] Temporary tables and CTEs used for intermediate results instead of nested subqueries > 2 levels
- [ ] Pagination uses keyset (`WHERE id > last_seen_id`) instead of `OFFSET` on large tables
- [ ] Functions applied to indexed columns in `WHERE` clauses noted (prevents index use)
- [ ] Transactions kept as short as possible — no user interaction or slow operations inside a transaction

### Data Integrity

- [ ] Foreign key constraints defined for all referential relationships
- [ ] `CHECK` constraints used for column-level business rules (e.g., `amount > 0`)
- [ ] `UNIQUE` constraints used instead of application-level uniqueness checks where possible
- [ ] Cascade behavior (`ON DELETE CASCADE`, `ON UPDATE CASCADE`) explicitly reviewed and documented
- [ ] Soft-delete columns (`deleted_at`) have a partial index so they don't bloat live-data queries
- [ ] Enum columns prefer a lookup/reference table with a foreign key over a raw CHECK constraint for extensibility
- [ ] Columns that default to `NULL` have that intent documented — unintentional nullability is a common data quality issue
