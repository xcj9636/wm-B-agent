# ADR 0001: Integrate OmniRoute as a shared internal gateway

- Status: Accepted
- Date: 2026-08-09

## Context

B-agent owns customer acquisition, sales workflows, channel delivery, RAG data,
and human takeover. OmniRoute provides an OpenAI-compatible inference gateway,
provider routing, fallback, quota, and usage visibility. Merging both source
trees would duplicate identity, workflow, memory, database, UI, and release
boundaries. A sidecar per API or worker process would also fragment the
gateway's local state and make routing and usage inconsistent.

## Decision

Run one shared OmniRoute service for each B-agent trust domain. B-agent API and
AI workers call it only through `LLMService` and a provider-neutral gateway
adapter. Skills, browser code, and business persistence must not depend on
OmniRoute DTOs or its SQLite database. Inference uses the `/v1/*` compatibility
surface; monitoring and provisioning use a separately versioned admin client.

Production policies resolve business use cases to explicit, persisted model
aliases. Dynamic `auto/*`, free/keyless pools, and fail-open candidate expansion
are prohibited for customer or conversation data. The direct provider adapter
remains a disabled break-glass path during the migration period.

## Consequences

- OmniRoute can be upgraded or rolled back independently behind contract tests.
- A gateway outage affects AI functions but must not take down non-AI B-agent APIs.
- The gateway is a deliberate single-instance dependency until its persistence
  model supports a tested active-active deployment.
- Provider selection cannot be supplied by a workflow or ordinary API caller.
