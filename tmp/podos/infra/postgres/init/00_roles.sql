-- Two roles, per ARCHITECTURE.md section 3.2 and the starter migration notes:
--   podos_owner: owns schema objects and runs migrations. Never used for request traffic.
--   podos_app:   runtime role for the API and workers. NOT an owner, NO BYPASSRLS.
CREATE ROLE podos_owner LOGIN PASSWORD 'podos_owner' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE podos_app   LOGIN PASSWORD 'podos_app'   NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;

CREATE DATABASE podos OWNER podos_owner;
CREATE DATABASE podos_test OWNER podos_owner;

\connect podos
CREATE EXTENSION IF NOT EXISTS pgcrypto;
ALTER SCHEMA public OWNER TO podos_owner;
GRANT USAGE ON SCHEMA public TO podos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE podos_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO podos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE podos_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO podos_app;

\connect podos_test
CREATE EXTENSION IF NOT EXISTS pgcrypto;
ALTER SCHEMA public OWNER TO podos_owner;
GRANT USAGE ON SCHEMA public TO podos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE podos_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO podos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE podos_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO podos_app;
