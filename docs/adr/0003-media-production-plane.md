# ADR 0003: Separate the media production plane from LLM routing

- Status: Accepted for implementation
- Date: 2026-08-11

## Context

B-agent uses OmniRoute and `LLMService` for text inference. Image and video
generation have different capability contracts, costs, file lifecycles,
durations, approval requirements, and unknown-outcome failure modes. Treating
them as ordinary chat completions would put provider credentials in the wrong
boundary and would hide expensive asynchronous side effects behind a text API.

The current production trust boundary is one organization per deployment, as
defined by ADR 0002. An `org_id` column is not proof of multi-tenant isolation.

## Decision

Introduce a provider-neutral media production plane:

- OmniRoute continues to handle text planning, scripts, storyboards, and prompt
  compilation.
- Media providers implement a separate capability and queue contract.
- Browser requests contain business intent only. Provider, model, organization,
  actor, sensitivity, policy version, and credentials are server-derived.
- Upload, planning, and external submission use separate feature flags that are
  disabled by default.
- Every external submission requires a short-lived, signed policy decision bound
  to the immutable attempt and input hash.
- Binary media is stored outside PostgreSQL, Redis, Agent Memory, and chat
  messages. Those stores contain identifiers, hashes, metadata, and lineage.
- This release remains single-organization. A multi-tenant release requires an
  Organization/Membership identity model and a separate ADR.

## Consequences

- Media inference can evolve without changing the LLM gateway contract.
- A UI flag cannot accidentally enable provider submission.
- Provider/model discovery never grants approval automatically.
- The system can distinguish business planning from expensive external effects.
- Later steps must add quarantined object storage and durable submission attempts
  before `MEDIA_SUBMIT_ENABLED` can be enabled in any deployed environment.
