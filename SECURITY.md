# Security Policy

## Reporting a Vulnerability

If you believe you have found a security vulnerability in moq-test-tracks or
in a hosted instance of it, please report it privately. **Do not open a public
GitHub issue for security reports.**

Email: security@openmoq.org

Please include:
- A description of the issue and its potential impact
- Steps to reproduce, or a proof of concept
- Affected version(s) (commit SHA or release tag) or the hosted instance URL
- Any suggested mitigations

We will acknowledge receipt within 5 business days and aim to provide an
initial assessment within 10 business days.

## Scope

In scope: the service (API, web UI, job runner, tool adapters), its container
images, and the deployment configuration in this repo.

Out of scope: vulnerabilities in the media tools it launches (aiomoqt, moxygen,
etc.) or in the relays it publishes to — report those to the owning project.
Reports that show how this service can be used to reach or amplify traffic
toward a third party via a tool or relay are in scope.

## Supported Versions

moq-test-tracks is in active development. Security fixes are applied to the
`main` branch and the most recent release. Older releases are not supported.
