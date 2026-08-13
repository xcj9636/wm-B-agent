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
- Provider callbacks are authenticated, deduplicated hints only; terminal state,
  quarantined output, and cost are derived through server-side provider reads.
- Provider billable units are stored as request-bound, unpriced evidence. They
  cannot become monetary cost until combined with an immutable account-specific
  pricing snapshot pinned to the same runtime revision.
- Successful request cost is settled only when units times pinned micro-USD price
  is exact and remains within the reservation. Failed requests without a result
  receipt stay unresolved instead of being reported as zero or as the estimate.
- The system can distinguish business planning from expensive external effects.
- Image-to-video accepts one durable reference asset identifier, never a
  browser-supplied URL. Immediately before the effect, the worker locks and
  reloads the promoted object plus live scan, rights, consent, and sensitivity
  evidence, verifies the organization-specific object namespace and current
  object SHA-256, size, and MIME, then creates an expiring provider-only read
  credential bound to the exact S3 VersionId. That URL is passed directly to
  the approved provider field and is never persisted or returned by an API.
- The promoted asset bucket must have versioning enabled and deployment policy
  must prevent overwriting or deleting reviewed versions during their retention
  window. Missing version identifiers and integrity drift fail before effect.
- Provider-input signing outages are operationally deferred; revoked or unsafe
  assets are terminal pre-effect denials. Reference-to-video and multi-reference
  payloads remain fail-closed until their separate schemas and policies exist.
- Later steps must add quarantined object storage and durable submission attempts
  before `MEDIA_SUBMIT_ENABLED` can be enabled in any deployed environment.
