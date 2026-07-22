\set ON_ERROR_STOP on

DO $role$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'automation_tool_operations') THEN
        CREATE ROLE automation_tool_operations LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
    END IF;
END
$role$;

ALTER ROLE automation_tool_operations NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
REVOKE ALL ON SCHEMA public FROM automation_tool_operations;
GRANT USAGE ON SCHEMA public TO automation_tool_operations;

GRANT SELECT, INSERT, UPDATE ON TABLE
    users,
    user_password_credentials,
    account_audit_events,
    account_session_families,
    account_session_tokens,
    account_login_rate_limits,
    account_recovery_tokens,
    installations,
    device_credentials,
    device_sessions
TO automation_tool_operations;
