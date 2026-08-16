# BeLib MVP E2E Wave 1A — Auth / Access Capability Audit

- Wave: `BELIB-E2E-W1-20260816`
- Lane: `A` (`auth_access`)
- Role: `implementation`
- Model: `gpt-5.6-luna`
- Reasoning: `high`
- Baseline: `checkpoint/belib-mvp-phase6-20260812`
- Baseline HEAD: `c20180e4887164a3dfcc78b977553eb78f655e04`
- Working branch: `test/e2e-auth-capability-audit-20260816`

## Capability Matrix

| ID | Feature | Expected | Observed | Capability Status | Failure Class | Evidence | Recommended Next Task |
|---|---|---|---|---|---|---|---|
| AUTH-01 | 管理者login | `/`へ遷移しDashboardを表示 | 管理者login後にDashboard表示 | PASS | — | Playwright test AUTH-01 | — |
| AUTH-02 | 一般ユーザーlogin | `/`へ遷移しuser roleで利用可能 | 一般ユーザーlogin後にDashboard表示、role=`user` | PASS | — | Playwright test AUTH-02 | — |
| AUTH-03 | logout後のprotected route | logout後のprotected routeを拒否 | `/admin`から`/login`へ遷移 | PASS | — | Playwright test AUTH-03 | — |
| AUTH-04 | 未認証protected API | protected APIは401 | `/api/v1/me`、`/api/v1/books`とも401 | PASS | — | Playwright test AUTH-04 | — |
| AUTH-05 | 未認証Reader直アクセス | loginへ遷移 | `/reader/:bookId`から`/login`へ遷移 | PASS | — | Playwright test AUTH-05 | — |
| AUTH-06 | 一般ユーザーのadmin画面 | admin画面を拒否 | `/admin`から`/`へ遷移 | PASS | — | Playwright test AUTH-06 | — |
| AUTH-07 | 管理者のadmin画面 | admin画面を表示 | `/admin`で「書籍管理」を表示 | PASS | — | Playwright test AUTH-07 | — |
| AUTH-08 | 自身のpassword change | 認証済み一般ユーザーが変更可能 | Better Auth `/api/auth/change-password`が200 | PASS | — | Playwright test AUTH-08 | UI操作入口の追加要否を別途判断 |
| AUTH-09 | password change後のlogin | 新passwordでlogin可能 | password変更後logoutし、新passwordでlogin成功 | PASS | — | Playwright test AUTH-09 | — |
| AUTH-10 | 利用停止ユーザーの新規login拒否 | sign-inを401で拒否 | `sign-in/email`が200を返した | FAIL | PRODUCT_FAILURE | Playwright assertion: expected 401, received 200 | Better Authのsign-in境界で`deletedAt`ユーザーを拒否する実装タスク |

## Result counts

```text
PASS = 9
FAIL = 1
NOT_IMPLEMENTED = 0
BLOCKED = 0
```

## Execution evidence

- Final E2E run: `9 passed, 1 failed` in `34.6s`.
- AUTH-10 failure is reproducible with an isolated `_e2e` PostgreSQL database and task-owned suspended user fixture.
- The failure is at the authentication sign-in response boundary; the test does not weaken the assertion or skip the capability.
- The shared `frontend/playwright.config.ts` and `frontend/e2e/start-servers.sh` were not changed.
- The repository's `.agents/SKILLS_INDEX.md` referenced by the prompt was absent at the baseline, so no repository-local `playwright-e2e` skill instructions were available.

## Metrics contract

```text
wave_id = BELIB-E2E-W1-20260816
lane = A
role = implementation
model = gpt-5.6-luna
reasoning = high
skill_name = playwright-e2e
workflow_name = skill-execution-metrics
data_kind = operational
parallel_execution = true
codex_credit.measurement_status = pending_external_snapshot
credits_consumed = null
cash_cost_jpy = null
```

## Next state

`junior_review_pending`
