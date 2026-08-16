# BeLib MVP Wave 1C — Admin Capability Audit

Date: 2026-08-16  
Baseline: `checkpoint/belib-mvp-phase6-20260812` at `c20180e4887164a3dfcc78b977553eb78f655e04`  
Working branch: `test/e2e-admin-capability-audit-20260816`  
Scope: browser user capability audit only; no product feature implementation

## Method

The audit uses `frontend/e2e/admin-capability-audit.spec.ts` with the existing real-runtime Playwright setup. Upload files are generated in test runtime and are not committed. A capability is `PASS` only when the browser UI reaches the API and the result is persisted in the database/storage boundary.

The baseline documentation states that the admin screen is still local state, while the backend exposes only minimal metadata and PDF registration endpoints. This audit keeps those states separate:

- `E2E成立`: UI → API → DB/storage was observed.
- `UIのみ`: the screen accepts input, but no admin API request or persistence was observed.
- `backend APIのみ`: an API behavior can be exercised directly, but no admin UI path reaches it.
- `未実装`: no browser-capable path exists for the requested MVP operation.

## Capability matrix

| ID | Capability | Status | Failure class | Evidence / classification |
| --- | --- | --- | --- | --- |
| BOOK-01 | 管理者が書籍登録画面を開く | PASS | — | `/admin` and registration sheet opened in browser |
| BOOK-02 | PDFを選択し登録 | NOT_IMPLEMENTED | NOT_RUN | UI shows local filename only; no admin API request |
| BOOK-03 | EPUBを選択し登録 | NOT_IMPLEMENTED | NOT_RUN | UI accepts EPUB locally; protected EPUB registration is not implemented |
| BOOK-04 | 200MB上限 | NOT_IMPLEMENTED | NOT_RUN | backend validator exists, but UI has no upload API path |
| BOOK-05 | 不正MIME / 内容を拒否 | NOT_IMPLEMENTED | NOT_RUN | backend rejects direct invalid uploads; UI cannot invoke it |
| BOOK-06 | 同一file hash重複を拒否 | NOT_IMPLEMENTED | NOT_RUN | direct API second upload returns 500; no UI path and no clear 409 contract |
| BOOK-07 | タイトルを登録 | NOT_IMPLEMENTED | NOT_RUN | title appears in local table only; DB list does not contain it |
| BOOK-08 | カテゴリを選択 | NOT_IMPLEMENTED | NOT_RUN | category appears in local table only; no category API/UI management |
| BOOK-09 | カテゴリ未選択時の正式処理 | NOT_IMPLEMENTED | NOT_RUN | no unset state; no browser-to-API registration path |
| BOOK-10 | publication scopeを必須選択 | NOT_IMPLEMENTED | NOT_RUN | no publication-scope field in registration UI or API |
| BOOK-11 | all-usersを設定 | NOT_IMPLEMENTED | NOT_RUN | no publication-scope field or API contract |
| BOOK-12 | admin-onlyを設定 | NOT_IMPLEMENTED | NOT_RUN | no publication-scope field or API contract |
| BOOK-13 | 登録後に対象ユーザー一覧へ反映 | NOT_IMPLEMENTED | NOT_RUN | local row is not persisted or reflected by backend list |
| BOOK-14 | metadataを編集 | NOT_IMPLEMENTED | NOT_RUN | no edit control or admin edit endpoint |
| CATEGORY-01 | カテゴリ一覧を管理 | NOT_IMPLEMENTED | NOT_RUN | no category management screen/endpoint |
| CATEGORY-02 | カテゴリを追加 | NOT_IMPLEMENTED | NOT_RUN | no category management screen/endpoint |
| CATEGORY-03 | 必要な編集操作 | NOT_IMPLEMENTED | NOT_RUN | no category management screen/endpoint |
| USER-01 | 一般ユーザーを登録 | NOT_IMPLEMENTED | NOT_RUN | no user management screen/endpoint |
| USER-02 | 一般ユーザーを利用停止 | NOT_IMPLEMENTED | NOT_RUN | no user management screen/endpoint |
| USER-03 | 一般ユーザーを再開 | NOT_IMPLEMENTED | NOT_RUN | no user management screen/endpoint |
| USER-04 | 仮passwordを再設定 | NOT_IMPLEMENTED | NOT_RUN | no user management screen/endpoint |
| DELETE-01 | 書籍を論理削除 | NOT_IMPLEMENTED | NOT_RUN | no delete control/endpoint |
| DELETE-02 | 通常一覧から消える | NOT_IMPLEMENTED | NOT_RUN | no delete flow |
| DELETE-03 | 削除済み一覧へ出る | NOT_IMPLEMENTED | NOT_RUN | no deleted-books screen/endpoint |
| DELETE-04 | 復元 | NOT_IMPLEMENTED | NOT_RUN | no restore control/endpoint |
| DELETE-05 | 復元後に再閲覧 | NOT_IMPLEMENTED | NOT_RUN | no restore flow |
| DELETE-06 | 完全削除 | NOT_IMPLEMENTED | NOT_RUN | no permanent-delete control/endpoint |
| REPLACE-01 | 登録済みfileを差し替え | NOT_IMPLEMENTED | NOT_RUN | no replacement control/endpoint |
| ACCESS-01 | 一般ユーザーはAdmin操作を実行できない | PASS | — | `/admin` redirects to `/`; admin POST returns 403 |

## Summary

```text
BOOK_REGISTRATION_PASS = 1/14
USER_MANAGEMENT_PASS = 0/4
CATEGORY_PASS = 0/3
PUBLICATION_SCOPE_PASS = 0/3
DELETE_RESTORE_PASS = 0/6
FILE_REPLACEMENT_PASS = 0/1
PASS = 2
FAIL = 0
NOT_IMPLEMENTED = 27
BLOCKED = 0
```

The two `PASS` results are limited to opening the existing admin screen and enforcing the existing admin boundary. No book-management capability in the requested scope currently satisfies the full UI → API → DB/storage criterion.

## Backend-only observations

- The existing backend accepts minimal book metadata with a category ID and creates a user-role permission, but the admin UI does not call this endpoint.
- The existing backend accepts PDF uploads, checks PDF MIME/name/header and the 200MB limit, and stores a local protected file plus `BookFile` metadata.
- Invalid MIME/content was observed as HTTP 400 through the direct API path.
- A duplicate file hash reaches the database unique constraint and currently surfaces as HTTP 500, not a clear duplicate response.
- EPUB, publication scope, category management, user management, delete/restore, permanent delete, and replacement have no browser-capable path in this baseline.

## Follow-up implementation candidates

1. Connect the admin registration UI to the existing metadata/PDF APIs and verify DB/storage read-back.
2. Add explicit publication scope (`all-users` / `admin-only`) with no default and role-based access tests.
3. Define category and general-user management API/UI contracts.
4. Add metadata edit and file replacement flows with reading-state reset rules.
5. Add logical delete/restore/permanent-delete flows with protected storage cleanup and failure handling.
6. Normalize duplicate hash handling to a clear conflict response after the product contract is approved.
7. Implement the R2 and EPUB boundaries before treating file registration as formal MVP-complete.

## Execution record

```text
BASELINE_HEAD = c20180e4887164a3dfcc78b977553eb78f655e04
FINAL_HEAD = reported in task completion after commit
BRANCH = test/e2e-admin-capability-audit-20260816
WAVE_ID = BELIB-E2E-W1-20260816
LANE = C
ROLE = implementation
MODEL = gpt-5.6-luna
REASONING = high
METRICS_RUN_ID = reported in task completion
METRICS_SAVE = pending
CREDIT_MEASUREMENT_STATUS = pending_external_snapshot
CREDITS_CONSUMED = null
CASH_COST_JPY = null
NEXT_STATE = junior_review_pending
```
