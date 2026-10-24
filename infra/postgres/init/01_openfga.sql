-- OpenFGA's own datastore (relationship tuples + authorization models). Separate role and
-- database: the PodOS runtime role has no access to it, and OpenFGA has none to PodOS tables.
CREATE ROLE openfga LOGIN PASSWORD 'openfga' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE DATABASE openfga OWNER openfga;
