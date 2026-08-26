# AGENTS.md

## Project

BeLib is a personal web application for managing and reading EPUB/PDF files in the browser.

Prioritize a small working MVP over broad feature coverage. Implement only what is needed for the requested task and avoid speculative features.

## Tech Stack

### Frontend

- React
- TypeScript
- Vite
- React Router
- Tailwind CSS
- shadcn/ui

### Backend

- Node.js
- Hono
- Prisma
- PostgreSQL
- Better Auth

## Current domain boundary

BeLib MVP focuses on:

- user authentication
- book management
- predefined category selection
- EPUB/PDF file metadata
- role-based book viewing permissions
- reading progress

Current Prisma models include:

### Authentication infrastructure

- User
- Session
- Account
- Verification
- Role

### BeLib domain

- Book
- Category
- BookFile
- ReadingInfo
- RoleBookPermission

Do not introduce second-phase tables unless explicitly requested. Examples include tags, bookmarks, notes, reviews, reading history, a separate authors table, shared URLs, and access logs.

## Source of truth

Use `backend/prisma/schema.prisma` as the source of truth for database structure.

When database structure changes, keep relevant artifacts in sync:

- `schema.prisma`
- new Prisma migrations
- generated client when required
- `docs/database.md`
- `docs/ER図.svg`
- related TypeScript/API definitions and tests

Important current rules:

- use `users.role_id`, not `users.role`
- do not add `books.role_id`
- use `role_book_permissions` for role-based book viewing permission
- use `book_files.book_id`
- use `reading_infos`
- `reading_infos.current_position` stays nullable text so PDF pages and future EPUB positions can share the model
- `reading_infos.read_status` defaults to `unread`
- `books.page_turn_direction` defaults to `ltr`
- credential storage is owned by the current Better Auth model; do not add a duplicate user credential column

## Documentation responsibilities

Read only the documents relevant to the task.

- target behavior / requirements: `docs/requirements.md`
- current implementation status and known drift: `docs/current-status.md`
- implemented `/api/v1` HTTP contract: `docs/api.yaml`
- execution order / completion gates: `docs/mvp_plan.md`
- UI/design: `docs/design.md`
- DB explanation: `docs/database.md`
- Japanese project-rule summary: `docs/AGENTS_ja.md`
- development naming/conventions: `docs/guidline.md`
- generated relations: `docs/ER図.svg`

For database structure, `schema.prisma` wins over prose documentation.

Do not rewrite a confirmed requirement merely because the current implementation is temporary or incomplete. Record implementation drift in `docs/current-status.md` and keep the requirement intact unless the user explicitly changes it.

`docs/old/` is historical material and should not be refreshed to look current.

## Development policy

### One feature, one meaningful verification

For feature implementations and bug fixes, add or update at least one relevant automated test when a meaningful test can be written.

A task is not complete until the behavior and its test or verification procedure are both addressed. Do not create meaningless tests merely to satisfy this rule.

Documentation-only, formatting-only, and simple text changes do not require automated tests unless they affect behavior.

### Implementation rules

- keep changes small and focused
- do not rewrite unrelated code
- do not rename files, models, routes, or components unless necessary
- do not change public behavior unless the task asks for it
- prefer readable code over clever code
- avoid premature abstraction
- preserve existing UI structure unless the task is about UI
- do not add libraries without a clear reason
- do not change the authentication strategy unless explicitly requested
- do not install a new test framework automatically

## Naming rules

- Prisma models: PascalCase singular
- DB tables: snake_case plural via `@@map`
- DB columns: snake_case via `@map` where needed
- TypeScript variables/functions: camelCase
- React components/types: PascalCase
- constants: UPPER_SNAKE_CASE when appropriate
- ordinary file names follow the existing local convention; do not force every file to PascalCase

## Database rules

- use foreign keys for relations
- use compound unique constraints where duplicate relationships must be prevented
- `ReadingInfo` is unique by `userId + bookId`
- `RoleBookPermission` is unique by `roleId + bookId`
- keep credential handling within the current authentication library/model boundary
- use existing logical deletion fields where already defined
- do not add user-specific book permissions unless explicitly requested; MVP uses role-based permissions
- do not modify an existing committed migration merely to make history match a later schema
- when schema changes are required, change schema first, create a forward migration, then validate fresh-DB reproducibility

## Reading status values

Use only:

- `unread`
- `reading`
- `completed`

Do not introduce a new status without synchronizing requirements, frontend, backend validation, API documentation, and tests.

## Page-turn direction values

Use only:

- `ltr`
- `rtl`

## Frontend rules

- keep components small and readable
- prefer explicit props over hidden global assumptions
- use existing loading/skeleton components when practical
- keep list/table behavior predictable
- use existing shadcn/ui components where practical
- avoid global state unless clearly needed
- do not change routing behavior unless requested
- distinguish production API paths from remaining mock/local-state screens

## Backend rules

- validate request input before database writes
- return clear errors for invalid input, missing records, and permission failures
- keep route handlers focused
- extract reusable logic only when reuse is real
- do not expose uploaded book files from a public directory without backend checks
- do not commit uploaded EPUB/PDF files
- protected local storage is acceptable for the current development vertical slice, but it does not cancel the formal storage requirements in `docs/requirements.md`

## Permission rules

Admin users may manage books/files through protected backend operations.
General users may view only books allowed by their role.

Book access must be enforced through `role_book_permissions` unless the permission model is explicitly changed. Frontend-only checks are insufficient.

## Testing rules

Before completing a behavior-changing task, run the smallest relevant available checks, then expand when needed.

Use scripts that actually exist in `package.json`. Do not invent commands.

Common available checks include backend/frontend tests, backend/frontend builds, frontend lint, Playwright E2E, and Prisma validation.

When schema changes are made, validate/format Prisma and verify migration consistency as appropriate.

If an automated test is not meaningful or cannot run, explain why and provide a manual verification procedure.

## Documentation rules

Update documentation when behavior, schema, setup, API, or accepted execution order changes.

Keep responsibilities separated:

- requirements = what must be true
- current-status = what is true now
- API = currently implemented contract
- schema = current DB structure
- MVP plan = completion order and gates
- records/Git history = dated execution evidence

Avoid copying volatile SHAs, PR states, and long test logs into enduring design/requirements documents. A status document may reference a checkpoint when needed to identify the inspected implementation.

## Git / change management

- keep diffs focused
- preserve unrelated existing changes
- inspect the relevant diff before saving
- do not include environment-specific values, local DB dumps, or uploaded book files
- summarize changes and verification honestly
- do not hide failing checks

## Final response format

When finishing a task, respond with:

1. Summary of changes
2. Tests/checks run
3. Notes or follow-up items

If something could not be completed, state it clearly.
