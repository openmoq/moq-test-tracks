# Gemini Code Review Guidelines for `moq-test-tracks`

`moq-test-tracks` is an on-demand MoQT media test track service and namespace codec (Python 3.12, FastAPI, uvicorn, SQLite, ffmpeg/tool runner subprocesses) implementing `draft-marzot-moq-test-tracks` and `draft-afrind-moq-test`.

## Review Format & Output Rules

- **Skip high-level PR summary poems and walkthroughs**: Keep your top-level review comment brief and focus strictly on **actionable inline findings**.
- **Prioritize high-severity defects**: Focus on security boundary enforcement, command/subprocess safety, namespace codec spec compliance, and resource/process-group lifecycle correctness. Do not post style or formatting nitpicks already enforced by `ruff` (`pyproject.toml`).
- **Review Event (`APPROVE` vs `COMMENT`)**: Override `<SUBMIT_REVIEW>` Step 3—when calling `submit_pending_pull_request_review`, pass `event: "APPROVE"` if you found no `CRITICAL`, `HIGH`, or `MEDIUM` issues (i.e. zero inline comments or only `LOW` findings); otherwise pass `event: "COMMENT"`.

## What to Prioritize

1. **Security Boundary & Subprocess Safety (`src/moq_test_tracks/**`, `adapters/**`, `config/**`, `deploy/**`, `docker/**`)**:
   - Refer to `docs/design.md#security-model` and `docs/tool-contract.md`.
   - **No shell execution**: Job launch and adapter invocations must use explicit `argv` lists (`shell=False`), validated against closed sets/ranges (`[a-z0-9_]` for namespace fields and tool IDs), with a sanitized environment containing no API keys or session secrets.
   - **SSRF & Relay Allowlist**: Relay targets must come from `config/relays.yaml` by ID, or (for `admin`-scoped custom URLs) be validated as `moqt://` or `https://` resolving strictly to public IP addresses (rejecting loopback, private, link-local, multicast, and CGNAT ranges in both `api` and `runner`).
   - **Auth & CSRF**: State-changing endpoints must be `POST` or `DELETE` (never `GET`), enforce `Origin` checks and `SameSite=Strict` cookies, and never accept or log API keys in query strings. Only SHA-256 hashes of API key secrets may be persisted.
   - **Process Group & TTL Supervision**: The runner owns job TTL enforcement and must supervise and terminate jobs by process group so `ffmpeg` and tool child processes cannot outlive their job or leak on restart.

2. **Namespace Codec & Tool Contract Compliance (`src/moq_test_tracks/spec/**`, `adapters/**`)**:
   - `src/moq_test_tracks/spec/` must remain a pure module with zero external service dependencies so external tools and probes can import it directly.
   - All 17 characteristic fields of `moq_test_tracks_00` must be explicit on the wire (no implicit defaults in the encoded namespace). Codec changes must preserve encode/decode round-trip invariants and include property/round-trip tests in `tests/`.
   - Tool adapters (`adapters/<tool>/`) must adhere to `docs/tool-contract.md` (`manifest.yaml`, `request.json`, status reporting, and exit code `64` for unsupported shapes).

3. **Repository Conventions (`CONTRIBUTING.md`)**:
   - **Changelog (`CHANGELOG.md`)**: User-visible changes (API endpoints, config keys, tuple fields, or tool adapters) must include an entry under `[Unreleased]`.
   - **License Header**: New source files must start with the 3-line OpenMOQ Apache 2.0 copyright header defined in `CONTRIBUTING.md`.
