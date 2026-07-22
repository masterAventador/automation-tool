import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function read(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("C10-03 fixes three non-privileged PostgreSQL identities without embedding secrets", async () => {
  const [roles, readme] = await Promise.all([
    read("deploy/postgresql/roles.sql"),
    read("deploy/postgresql/README.md"),
  ]);

  for (const role of [
    "automation_tool_migrator",
    "automation_tool_app",
    "automation_tool_backup",
  ]) {
    assert.match(roles, new RegExp(`CREATE ROLE ${role}`, "u"));
  }
  for (const restriction of [
    "NOSUPERUSER",
    "NOCREATEDB",
    "NOCREATEROLE",
    "NOREPLICATION",
    "NOBYPASSRLS",
  ]) {
    assert.ok(roles.includes(restriction), `${restriction} is missing`);
  }
  assert.doesNotMatch(roles, /PASSWORD|TRUST|pg_read_all_data|pg_write_all_data/ui);
  assert.doesNotMatch(readme, /PGPASSWORD|--password|postgresql[^\s]*:[^\s]*@/u);
  assert.match(readme, /Secret Store/u);
  assert.match(readme, /私网/u);
});

test("C10-03 grants DDL DML and backup rights to distinct identities", async () => {
  const privileges = await read("deploy/postgresql/privileges.sql");

  assert.match(privileges, /ALTER SCHEMA public OWNER TO automation_tool_migrator/u);
  assert.match(privileges, /REVOKE CREATE ON SCHEMA public FROM PUBLIC/u);
  assert.match(
    privileges,
    /GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO automation_tool_app/u,
  );
  assert.match(
    privileges,
    /GRANT SELECT ON ALL TABLES IN SCHEMA public TO automation_tool_backup/u,
  );
  assert.match(privileges, /ALTER DEFAULT PRIVILEGES FOR ROLE automation_tool_migrator/u);
  assert.doesNotMatch(privileges, /GRANT (?:ALL|CREATE).*automation_tool_(?:app|backup)/u);
});

test("C10-03 proves private migration backup and isolated restore with real PostgreSQL", async () => {
  const acceptance = await read("scripts/run_c10_03_acceptance.py");

  assert.match(acceptance, /automation-tool-c10-03-primary/u);
  assert.match(acceptance, /automation-tool-c10-03-restore/u);
  assert.match(acceptance, /alembic[\s\S]*upgrade[\s\S]*head/u);
  assert.match(acceptance, /pg_dump/u);
  assert.match(acceptance, /pg_restore/u);
  assert.match(acceptance, /PGPASSFILE/u);
  assert.match(acceptance, /--no-owner/u);
  assert.match(acceptance, /--no-acl/u);
  assert.match(acceptance, /PortBindings/u);
  assert.doesNotMatch(acceptance, /--publish|-p["']/u);
});
