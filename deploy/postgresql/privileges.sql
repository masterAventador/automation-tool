\set ON_ERROR_STOP on

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM automation_tool_app;
REVOKE ALL ON SCHEMA public FROM automation_tool_backup;
ALTER SCHEMA public OWNER TO automation_tool_migrator;

GRANT USAGE ON SCHEMA public TO automation_tool_app;
GRANT USAGE ON SCHEMA public TO automation_tool_backup;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO automation_tool_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO automation_tool_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO automation_tool_app;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO automation_tool_backup;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO automation_tool_backup;

ALTER DEFAULT PRIVILEGES FOR ROLE automation_tool_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO automation_tool_app;
ALTER DEFAULT PRIVILEGES FOR ROLE automation_tool_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO automation_tool_app;
ALTER DEFAULT PRIVILEGES FOR ROLE automation_tool_migrator IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO automation_tool_app;
ALTER DEFAULT PRIVILEGES FOR ROLE automation_tool_migrator IN SCHEMA public
    GRANT SELECT ON TABLES TO automation_tool_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE automation_tool_migrator IN SCHEMA public
    GRANT SELECT ON SEQUENCES TO automation_tool_backup;
