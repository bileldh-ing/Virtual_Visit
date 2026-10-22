# Security policy

## Reporting a vulnerability
Email **dhahribilel1807@gmail.com** with "SECURITY" in the subject. Please do not open public issues for
vulnerabilities. We acknowledge within 72 hours and aim to fix critical issues within 14 days.

## Design summary
| Area | Control |
|---|---|
| Sessions | Opaque 256-bit tokens in `HttpOnly`, `SameSite=Lax` cookies (`Secure` when `ROOMSCAPE_COOKIE_SECURE=true`); only SHA-256 hashes stored; password reset revokes every session |
| Passwords | Argon2id; minimum 10 chars with mixed case and a digit; rate-limited sign-in per IP and per email |
| CSRF | Cookie-authenticated unsafe requests must carry `X-Requested-With: roomscape`; CORS restricted to the web origin |
| Authorization | Workspace roles OWNER > ADMIN > EDITOR > VIEWER checked server-side on every route; platform admin is separate |
| Files | Private bucket; every file URL is HMAC-signed with expiry; share-scoped URLs die when a link is revoked |
| Privacy | Originals keep EXIF but are never served on share links; web/thumb derivatives are EXIF-free; private address is never in public payloads; publishing requires a verified email and explicit consent |
| Share links | 192-bit random tokens (hash stored), expiry, revocation, optional Argon2 password with short-lived signed unlock token, embed origin allow-list |
| Uploads | Size limits, magic-byte validation and full decode, duplicate detection, per-plan storage quota |
| Deletion | Project deletion revokes links, cancels jobs and deletes storage keys; completion is verified by listing the prefix |
| Audit | Security-relevant actions are written to an audit table (actor, target, request id) |
| Web | Strict CSP, `X-Frame-Options: DENY` (except `/embed`), `nosniff`, referrer policy, permissions policy |
| Supply chain | CodeQL, pip-audit, pnpm audit, gitleaks, Trivy (filesystem + images), Dependabot |

## Known limitations
* Embed origin allow-lists are enforced in the app; a CSP `frame-ancestors` header per link is not yet emitted.
* Rate limiting falls back to in-process counters when Redis is unavailable.
* The development outbox endpoint (`/api/v1/dev/outbox`) is disabled unless `ROOMSCAPE_ENV` is development or test.
