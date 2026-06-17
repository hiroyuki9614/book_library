# AGENTS.md

## Project

BeLib is a personal web application for managing and reading EPUB/PDF files in the browser.

The project prioritizes a small, working MVP over broad feature coverage.
Implement only what is necessary for the requested task, and avoid adding speculative features.

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

## Core Domain

The MVP focuses on these features:

- User authentication
- Book management
- Category selection from predefined categories
- EPUB/PDF file metadata management
- Role-based book viewing permissions
- Reading progress management

The current MVP database models are:

- User
- Role
- Book
- Category
- BookFile
- ReadingInfo
- RoleBookPermission

Do not introduce second-phase tables unless explicitly requested.
Examples of second-phase features are tags, bookmarks, notes, reviews, reading history, authors table, shared URLs, and access logs.

## Source of Truth

Use `schema.prisma` as the source of truth for database structure.

When changing database-related code, keep these in sync when relevant:

- `schema.prisma`
- Prisma migrations
- database definition documentation
- ER diagram
- related TypeScript types

Do not keep old columns or duplicate permission logic.

In particular:

- Use `users.role_id`, not `users.role`.
- Do not add `books.role_id`.
- Use `role_book_permissions` for role-based book viewing permissions.
- Use `book_files.book_id`, not `book_files.book_file_id`.
- Use `reading_infos`, not `reading_statuses`.
- `reading_infos.current_position` must be nullable text because EPUB CFI may be stored.
- `reading_infos.read_status` defaults to `unread`.
- `books.page_turn_direction` defaults to `ltr`.

## Development Policy

### One feature, one test

For every feature implementation or bug fix, add or update at least one relevant test when a meaningful automated test can be written.

A task is not complete unless the feature and its corresponding test or verification procedure are both addressed.
If a meaningful automated test cannot be added, explain the reason in the final response and provide a manual verification procedure.

Examples:

- Adding a category API requires a category API test.
- Adding book list filtering requires a filtering test.
- Adding reading progress update logic requires a reading progress test.
- Fixing a permission bug requires a permission regression test.

Do not batch many unrelated features into one test.
Prefer small tests that verify one behavior clearly.

Do not create meaningless tests only to satisfy the rule.
Documentation-only changes, formatting-only changes, and simple text changes do not require automated tests unless they affect behavior.

## Implementation Rules

- Keep changes small and focused.
- Do not rewrite unrelated code.
- Do not rename files, models, routes, or components unless necessary.
- Do not change public behavior unless the task asks for it.
- Prefer readable code over clever code.
- Avoid premature abstraction.
- Preserve existing UI structure unless the task is about UI changes.
- Do not add new libraries without a clear reason.
- When adding a library, explain why it is necessary.
- Do not introduce or change the authentication strategy unless explicitly requested.
- Do not install a new test framework automatically. If no test command exists, explain it and provide manual verification steps.

## Naming Rules

- Prisma model names use PascalCase singular names.
- Database table names use snake_case plural names via `@@map`.
- Database column names use snake_case via `@map` where needed.
- TypeScript variables and functions use camelCase.
- React components use PascalCase.
- Constants use UPPER_SNAKE_CASE.

Examples:

- Prisma model: `RoleBookPermission`
- Database table: `role_book_permissions`
- TypeScript variable: `roleBookPermission`

## Database Rules

- Use foreign keys for relations.
- Use compound unique constraints where duplicate relationships must be prevented.
- `ReadingInfo` must be unique by `userId` and `bookId`.
- `RoleBookPermission` must be unique by `roleId` and `bookId`.
- Do not store plain text passwords. Use `password_hash`.
- Use logical deletion with `deleted_at` where already defined.
- Do not add user-specific book permissions unless explicitly requested. The MVP uses role-based permissions.
- Do not modify existing migration files after they have been committed unless explicitly requested.
- If a schema change is required, update the schema first, then validate it.

## Reading Status Values

Use only these values for `read_status`:

- `unread`
- `reading`
- `completed`

Do not introduce new status values without updating the documentation, frontend filters, backend validation, and tests.

## Page Turn Direction Values

Use only these values for `page_turn_direction`:

- `ltr`: left to right
- `rtl`: right to left

Do not use `left`, `right`, `horizontal`, or other ambiguous values.

## Frontend Rules

- Keep components small and readable.
- Prefer explicit props over hidden global assumptions.
- Loading states should use existing skeleton components when available.
- Keep table/list behavior predictable.
- Avoid changing layout tokens or design rules unless the task asks for design changes.
- Use existing shadcn/ui components where practical.
- Avoid introducing global state unless the task clearly requires it.
- Do not change routing behavior unless the task asks for it.

## Backend Rules

- Validate request input before database writes.
- Return clear error responses for invalid input, missing records, and permission failures.
- Keep route handlers small.
- Put reusable logic into service/helper functions only when reuse is real.
- Do not expose uploaded files directly from a public directory without authentication and permission checks.
- Uploaded EPUB/PDF files must not be committed to the repository.
- Use local ignored storage for development files unless the task explicitly asks for test fixtures.

## Permission Rules

Admin users may manage books and files.
General users may only view books allowed by their role.

Book access must be checked through `role_book_permissions` unless the task explicitly changes the permission model.

Do not rely on frontend-only permission checks.
Backend permission checks are required for protected operations.

## Testing Rules

Before completing a task, run the most relevant available checks.
Prefer the smallest command that verifies the change.

Common checks may include:

- `npm run test`
- `npm run lint`
- `npm run typecheck`
- `npx prisma validate`
- `npx prisma format`

If a command does not exist, do not invent it.
Inspect `package.json` and use the available scripts.

If no test framework exists for the touched area, do not install one automatically.
Instead, explain that no automated test command exists and provide a manual verification procedure.

When database schema changes are made, run:

- `npx prisma format`
- `npx prisma validate`

After running `prisma format`, include any formatting changes in the diff.

If migrations are required, create them only when the task explicitly asks or when schema changes are part of the task.

## Documentation Rules

Update documentation when behavior, schema, or setup changes.

For database changes, update:

- Database definition document
- ER diagram if generated documentation is used
- Any seed data notes if relevant

Prefer generating documentation from `schema.prisma` where possible.

## Git / Change Management

- Keep diffs focused.
- Summarize what changed.
- Mention tests run and their results.
- Mention tests not run and why.
- Do not hide failing tests.
- Do not commit secrets, `.env` files, local database dumps, or uploaded book files.
- Do not include generated files in the diff unless they are expected project artifacts.

## Final Response Format

When finishing a task, respond with:

1. Summary of changes
2. Tests/checks run
3. Notes or follow-up items

If something could not be completed, state it clearly.
