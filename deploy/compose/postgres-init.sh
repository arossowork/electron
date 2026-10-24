#!/bin/sh
# Runs once, on first start of an empty data volume.
set -eu
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres <<SQL
CREATE ROLE hwflow LOGIN PASSWORD '${HWFLOW_DB_PASSWORD}';
-- hwplatform API: owns nothing, so row-level security applies (services/platform/README.md).
CREATE ROLE hwflow_app LOGIN PASSWORD '${HWFLOW_APP_DB_PASSWORD}';
-- hwplatform outbox relay: sees every tenant's outbox rows (policy relay_all), nothing else.
CREATE ROLE hwflow_relay LOGIN PASSWORD '${HWFLOW_RELAY_DB_PASSWORD}';
CREATE ROLE temporal LOGIN PASSWORD '${TEMPORAL_DB_PASSWORD}';
CREATE DATABASE hwflow OWNER hwflow;
CREATE DATABASE temporal OWNER temporal;
CREATE DATABASE temporal_visibility OWNER temporal;
SQL
