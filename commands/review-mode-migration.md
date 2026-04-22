# Review Mode: Migration

**Applies when:** Changed files include `**/migrations/**`, `*.sql`, `**/alembic/**`, or PR label `migration` or `db-change`.

**Severity multipliers:** All findings elevated to `critical`. Migration failures in production can cause data loss, downtime, and are often irreversible.

---

## Checklist

### Data Loss Risk
- [ ] Destructive DDL: `DROP TABLE`, `DROP COLUMN`, `TRUNCATE` — is there a data archival step first?
- [ ] Column type narrowing: changing `TEXT` → `VARCHAR(50)` or `BIGINT` → `INT` can silently truncate data
- [ ] NOT NULL added to column: existing rows will fail if no DEFAULT is set or backfill is missing
- [ ] Default value change: existing rows are not retroactively updated — confirm expected behavior
- [ ] Cascade delete: new `ON DELETE CASCADE` constraint — are all affected child rows acceptable to lose?

### Rollback Safety
- [ ] Is there a corresponding down migration (`def downgrade()` in Alembic, `-- rollback:` block in SQL)?
- [ ] Is the down migration actually safe? Can deleted columns/tables be restored? Is the data gone?
- [ ] Can the migration be applied and rolled back without downtime? (Blue/green, zero-downtime migrations)
- [ ] Rollback tested: if the down migration drops a column added by up, confirm no data loss on round-trip

### Lock Duration and Availability
- [ ] Table-level locks: `ALTER TABLE` on large tables can lock for minutes — is `LOCK TIMEOUT` set?
- [ ] Index creation: `CREATE INDEX` without `CONCURRENTLY` locks writes on PostgreSQL
- [ ] Full-table scans: backfills that `UPDATE` every row will lock rows progressively — use batching
- [ ] Migration duration estimate: for tables >1M rows, estimate time and confirm acceptable

### Idempotency
- [ ] Migration is idempotent: running it twice does not corrupt data or fail
- [ ] `IF NOT EXISTS` / `IF EXISTS` guards on `CREATE TABLE`, `CREATE INDEX`, `DROP COLUMN` where applicable
- [ ] Sequence/auto-increment resets: resetting a sequence can cause PK collisions if rows exist

### Schema Compatibility (Zero-Downtime Deployments)
- [ ] Additive-only during deploy: new columns should be nullable or have defaults before code depends on them
- [ ] Old code compatibility: if old app version runs against new schema during rolling deploy, does it break?
- [ ] Column rename: renaming a column requires a dual-write period, not a single-step rename
- [ ] Foreign key added: existing rows must satisfy the constraint — check for orphaned records first

### Data Integrity
- [ ] Unique constraint added: duplicate values in existing data will cause the migration to fail
- [ ] Check constraint added: existing data must satisfy the constraint before it can be applied
- [ ] Index on expression: confirm expression is deterministic and doesn't fail on NULL inputs

---

## Confidence Guidance

In migration mode, flag at 0.75+ confidence. The cost of a missed data-loss bug in a migration is catastrophic and often irreversible. False positives in this mode are acceptable — the developer can annotate with `# cr: intentional` if the concern is addressed.
