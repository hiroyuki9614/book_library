# Test-Last Auth Existing-Session Probe — 2026-08-23

- skill: `test-last 0.1.0-experimental`
- implementation_head_sha: `6b49faea2044a54e561eae3aa4fd5cc9fd6d0590`
- test_evidence_branch: `test/test-last-auth-existing-session-20260823`
- oracle: `docs/requirements.md` section 6.2

## Contract under test

- suspended users cannot create a new login session
- sessions that already existed before suspension remain usable until their normal expiry

## Test-Last boundary

This probe adds test evidence only. Production source is not modified. If a focused test finds an implementation defect, correction must happen in a separate implementation phase with a new implementation identity.

## Added risk case

1. an authenticated session already exists for a general user
2. the user is suspended (`deletedAt` becomes non-null)
3. `/api/v1/me` is called using that existing session
4. expected: the session remains accepted until expiry

The existing session-creation tests remain responsible for the complementary new-login rejection contract.
