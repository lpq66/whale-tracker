# Errors

Command failures and integration errors.

---

## [ERR-20260328-001] moltbook_api

**Logged**: 2026-03-28T15:25:00Z
**Priority**: medium
**Status**: pending
**Area**: infra

### Summary
Moltbook registration returning 500 errors for some agent names

### Error
{"statusCode":500,"message":"Internal server error"}

### Context
- First attempts with "Ash" and "Ash_OpenClaw" returned 500
- "Ash_OpenClaw" later returned 409 (name taken) — suggesting the 500 may have been partial success
- "AshWaltBot" succeeded on first try
- Multiple API endpoints timing out or returning 500 intermittently

### Suggested Fix
May be early-stage platform instability. Retry with different names if 500 encountered. Check for 409 (name taken) as a separate case.

### Metadata
- Reproducible: unknown
- Tags: moltbook, api, registration

---

## [ERR-20260328-002] moltbook_api

**Logged**: 2026-03-28T15:40:00Z
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
Moltbook /home, /feed, and search endpoints timing out or returning 500

### Error
Connection timeouts and HTTP 500 responses on multiple endpoints

### Context
- GET /api/v1/home — 500 or timeout
- GET /api/v1/posts?submolt=trading — empty response/timeout
- GET /api/v1/search — timeout
- GET /api/v1/submolts — worked fine
- POST /api/v1/posts — worked fine
- POST /api/v1/verify — worked fine

### Suggested Fix
Write endpoints seem more reliable than read endpoints. Check Moltbook status before batch operations. May stabilize as platform matures.

### Metadata
- Reproducible: unknown
- Tags: moltbook, api, reliability
