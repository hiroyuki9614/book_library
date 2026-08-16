# BeLib MVP E2E Wave 1B — General User / Library / Reader Capability Audit

## Run identity

```text
BASELINE_HEAD = c20180e4887164a3dfcc78b977553eb78f655e04
BRANCH = test/e2e-general-user-capability-audit-20260816
WAVE_ID = BELIB-E2E-W1-20260816
LANE = B
ROLE = implementation
MODEL = gpt-5.6-luna
REASONING = high
```

The baseline branch `checkpoint/belib-mvp-phase6-20260812`, local HEAD, and `origin` ref all matched the requested baseline SHA before work began.

## Test scope and evidence

The existing real-runtime vertical test was extended rather than duplicated. The fixture now contains:

- one permitted general user and a second permitted peer user;
- one unpermissioned general user;
- one admin-only book;
- one generated two-page PDF in ignored local storage.

The test exercises real browser navigation, Better Auth cookies, protected API calls, and PostgreSQL-backed reading-info persistence. No fixed-time wait was added; assertions wait for URL, response, or visible state.

Executed command:

```text
cd frontend
E2E_DATABASE_URL=postgresql://test_user:***@127.0.0.1:5433/belib_wave1b_run3_e2e \
E2E_ADMIN_EMAIL=... E2E_ADMIN_PASSWORD=... E2E_MVP_PASSWORD=... \
E2E_BOOK_FILE_STORAGE_ROOT=/tmp/belib-wave1b-run3-storage \
E2E_MVP_METADATA_PATH=/tmp/belib-wave1b-run3/metadata.json \
npm run test:e2e -- real-mvp-vertical.spec.ts --workers=1 --reporter=line
```

Result: **3 passed** in approximately 1 minute.

The first attempt exposed a test setup failure because the existing spec read fixture metadata before Playwright started `webServer`. The metadata read was moved into `test.beforeAll`; this was classified as `TEST_FAILURE`, not a product failure. A second invocation from repository root also exposed the existing relative-cwd startup contract; the final run used the frontend package cwd without changing shared Playwright infrastructure.

## Capability matrix

| Capability | Status | Failure class | Evidence / reason |
| --- | --- | --- | --- |
| LIB-01 login後、閲覧可能な書籍一覧が表示される | PASS | — | Permissioned user login and permitted title visible in `real-mvp-vertical.spec.ts`. |
| LIB-02 admin-only書籍が一般ユーザー一覧に表示されない | PASS | — | Admin-only fixture title absent for permissioned and unpermissioned users. |
| LIB-03 admin-only書籍 detail/file direct access is rejected | PASS | — | Both detail and file requests return 403 for unpermissioned user. |
| LIB-04 タイトル検索 | NOT_IMPLEMENTED | NOT_RUN | Current `/api/v1/books` has no search query contract; UI list remains local filtering. |
| LIB-05 著者検索 | NOT_IMPLEMENTED | NOT_RUN | Current `/api/v1/books` has no author search implementation; fixture has no server-backed search path. |
| LIB-06 カテゴリ絞り込み | NOT_IMPLEMENTED | NOT_RUN | Current `/api/v1/books` has no category filter contract. |
| PDF-01 一覧からPDFを開ける | PASS | — | Permitted title navigates to `/reader/:id`; protected PDF response is 200. |
| PDF-02 protected PDF is obtained after auth/authz | PASS | — | Permissioned user receives `application/pdf`; unauthorized access receives 403. |
| PDF-03 ページ移動 | PASS | — | Two-page PDF moves from page 1 to page 2. |
| PDF-04 current position is saved to PostgreSQL | PASS | — | PATCH returns page 2/`reading`; subsequent authenticated GET reads page 2/`reading`. |
| PDF-05 reload/revisit restores position | PASS | — | Browser reload restores page 2 from reading-info GET. |
| PDF-06 first view becomes `reading` | PASS | — | Real PATCH response is asserted with `readStatus: reading`. |
| PDF-07 final page becomes `completed` | NOT_IMPLEMENTED | NOT_RUN | Current backend PATCH always writes `reading`; no final-page transition exists. |
| PDF-08 completed remains after moving back | NOT_IMPLEMENTED | NOT_RUN | Depends on the missing completed transition. |
| PROGRESS-01 own status/progress is visible in the prescribed UI | NOT_IMPLEMENTED | NOT_RUN | Home summary/progress currently uses local mock reading-progress data rather than the authenticated server record. |
| PROGRESS-02 another user's ReadingInfo is inaccessible | PASS | — | Peer user with the same book permission receives its own default page 1/`unread`, not the first user's saved page 2/`reading`. |
| EPUB-01 permitted EPUB opens | NOT_IMPLEMENTED | NOT_RUN | No protected EPUB file route/fixture is available. |
| EPUB-02 protected EPUB retrieval | NOT_IMPLEMENTED | NOT_RUN | Current protected file endpoint is PDF-specific. |
| EPUB-03 reader displays real EPUB | NOT_IMPLEMENTED | NOT_RUN | Existing EPUB reader is not connected to a server EPUB path. |
| EPUB-04 EPUB position saves to server | NOT_IMPLEMENTED | NOT_RUN | Existing EPUB reader stores location locally; no server progress route is connected. |
| EPUB-05 reload/revisit restores EPUB position | NOT_IMPLEMENTED | NOT_RUN | Server-backed EPUB position persistence is absent. |
| EPUB-06 EPUB uses the same permission boundary | NOT_IMPLEMENTED | NOT_RUN | No protected EPUB backend path exists to exercise the boundary. |

## Totals

```text
LIBRARY_PASS = 3/6
PDF_PASS = 6/8
PROGRESS_PASS = 1/2
EPUB_PASS = 0/6
PASS = 10
FAIL = 0
NOT_IMPLEMENTED = 12
BLOCKED = 0
```

## PRODUCT_FAILURE / NOT_IMPLEMENTED

No `PRODUCT_FAILURE` was observed in the executed capabilities. The fixed `NOT_IMPLEMENTED` items are search, category filtering, completed transitions, server-backed progress UI, and all protected EPUB capabilities listed above. The audit did not implement product features.

## Follow-up implementation candidates

1. Add server-backed title/author/category query handling and connect the library UI to it.
2. Join the current user's `ReadingInfo` when building the book list response.
3. Implement PDF final-page `completed` transition with completed-state preservation.
4. Replace mock progress summary data with authenticated API data.
5. Connect EPUB registration, protected delivery, reader position persistence, and the shared permission boundary.

## Metrics

```text
METRICS_RUN_ID = pending
METRICS_SAVE = pending
CREDIT_MEASUREMENT_STATUS = pending_external_snapshot
CREDITS_CONSUMED = null
CASH_COST_JPY = null
NEXT_STATE = junior_review_pending
```
