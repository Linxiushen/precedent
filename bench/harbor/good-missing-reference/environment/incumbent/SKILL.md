---
name: db-migrate
description: Run a migration against a named environment with a rollback ready
scope: project
---

# Apply a database migration

## When to use

Use this when a schema change has to reach an environment and a rollback has to exist before it does.

## Procedure

1. Generate the migration and read the SQL it will run.
2. Confirm with the user before you apply a migration to anything but a scratch database; do not proceed without an explicit yes.
3. Run `scripts/migrate.sh` to check that the working copy has a migrations directory.
4. Apply only the migrations named on the command line; leave every later migration pending.

## Rules

- Always use `migrate up --one` for moving the schema forward.
- Never use `psql -f`: raw SQL bypasses the migration ledger and cannot be rolled back.

## Evidence

Verified by running `scripts/migrate.sh --check` against the fixture in this bundle.
