# ruff: noqa: E501
"""Admin-only provisioning shared by migration and the private host provisioner."""

WORKER_PRIVILEGES_SQL = """
DO $$
DECLARE
    inherited_role RECORD;
    runtime_oid OID;
    app_schema TEXT := current_schema();
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fatty_worker') THEN
        CREATE ROLE fatty_worker NOLOGIN;
    END IF;
    ALTER ROLE fatty_worker NOSUPERUSER NOCREATEDB NOCREATEROLE
        NOINHERIT NOREPLICATION NOBYPASSRLS;
    SELECT oid INTO runtime_oid FROM pg_roles WHERE rolname = 'fatty_worker';
    IF EXISTS (SELECT 1 FROM pg_database WHERE datdba = runtime_oid)
       OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = runtime_oid)
       OR EXISTS (SELECT 1 FROM pg_class WHERE relowner = runtime_oid)
       OR EXISTS (SELECT 1 FROM pg_proc WHERE proowner = runtime_oid) THEN
        RAISE EXCEPTION 'fatty_worker must not own database objects';
    END IF;
    -- NOINHERIT alone does not prevent SET ROLE into a privileged membership.
    FOR inherited_role IN
        SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid
        WHERE m.member = runtime_oid
    LOOP
        EXECUTE format('REVOKE %I FROM fatty_worker', inherited_role.rolname);
    END LOOP;
    EXECUTE format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC, fatty_worker', current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO fatty_worker', current_database());
    EXECUTE format('REVOKE ALL ON SCHEMA %I FROM fatty_worker', app_schema);
    EXECUTE format('REVOKE CREATE ON SCHEMA %I FROM PUBLIC', app_schema);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO fatty_worker', app_schema);
    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM fatty_worker', app_schema);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO fatty_worker', app_schema);
    EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM fatty_worker', app_schema);
    EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO fatty_worker', app_schema);
    -- New tables belong to the migrator, never to the runtime principal.
    EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I REVOKE ALL ON TABLES FROM fatty_worker', current_user, app_schema);
    EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I REVOKE ALL ON SEQUENCES FROM fatty_worker', current_user, app_schema);
    EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO fatty_worker', current_user, app_schema);
    EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT USAGE, SELECT ON SEQUENCES TO fatty_worker', current_user, app_schema);
    IF to_regclass(format('%I.schema_migrations', app_schema)) IS NOT NULL THEN
        EXECUTE format('REVOKE ALL ON TABLE %I.schema_migrations FROM fatty_worker', app_schema);
        EXECUTE format('GRANT SELECT ON TABLE %I.schema_migrations TO fatty_worker', app_schema);
    END IF;
END
$$;
"""
