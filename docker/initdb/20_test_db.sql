-- A separate database for the test suite, so tests can wipe tables without touching dev data.
CREATE DATABASE geo_test;
\connect geo_test
CREATE EXTENSION IF NOT EXISTS postgis;
