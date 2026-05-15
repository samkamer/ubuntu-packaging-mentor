# Canary Package — Guardian Validation Fixture

**NOT FOR UPLOAD. DO NOT USE IN PRODUCTION.**

This package contains deliberately fake secrets and missing compiler hardening
flags. It exists solely to validate the `guardian.py` security agent end-to-end
against known ground truth.

## Expected findings (guardian ground truth)

| File | Line | Type | Severity |
|------|------|------|----------|
| `creds/aws_config.ini` | 4 | `aws_access_key_id` | critical |
| `creds/aws_config.ini` | 5 | `aws_secret_key` | critical |
| `config/settings.py` | 2 | `api_key` | high |
| `config/settings.py` | 3 | `hardcoded_password` | high |
| `deploy/github_token.sh` | 3 | `github_pat_classic` | critical |

**Expected score:** 0/100  **Verdict:** fail

## Credential test vectors

All credentials are well-known public test vectors or obvious placeholders:

- **AWS key ID**: `AKIA...7EXAMPLE` (official AWS documentation example, 20 chars)
- **AWS secret**: `wJalr...EXAMPLEKEY` (official AWS documentation example, 40 chars)
- **GitHub PAT**: `ghp_` + 36 identical uppercase letters — obviously fake
- **API key**: 24-char hex placeholder
- **Password**: classic placeholder value (derivation of "hunter2")

None of these credentials are valid or have ever been valid.

## Hardening

`debian/rules` intentionally omits `export DEB_BUILD_MAINT_OPTIONS = hardening=+all`
so that blhc reports missing hardening flags on a real build log.
See `tests/fixtures/` for synthetic build logs used in integration tests.
