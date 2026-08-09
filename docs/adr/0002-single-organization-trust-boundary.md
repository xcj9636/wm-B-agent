# ADR 0002: Use a single-organization trust boundary

- Status: Accepted for the first production architecture
- Date: 2026-08-09

## Context

The selected OmniRoute v3 architecture has shared local configuration and
persistence. Separate API keys provide authentication and permissions but do
not prove complete tenant isolation for provider credentials, request logs, or
usage state.

## Decision

One B-agent deployment serves one organization/trust domain and connects to one
dedicated OmniRoute instance and data volume. Only B-agent backend and worker
networks may reach its inference port. Provider credentials stay in the gateway
secret boundary and never enter browser storage or B-agent business tables.

A future SaaS/multi-tenant deployment requires a new ADR. Until an upstream
isolation model is independently verified, each tenant or trust domain must use
an isolated OmniRoute instance and volume; API keys alone are not an isolation
control.

## Consequences

- The current design is suitable for private single-organization deployments.
- Shared multi-customer SaaS hosting is explicitly outside this release scope.
- Backups, restores, retention, and upgrades operate per trust-domain volume.
