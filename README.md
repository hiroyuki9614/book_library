# BeLib

BeLib は、認証・ロール別閲覧権限・読書進捗を備えた個人向け EPUB / PDF ライブラリを目指すフルスタックWebアプリです。

現在は **PDF/EPUB閲覧とnon-R2の管理コアを実装済み** で、正式MVPに向けてR2、ファイル lifecycle、最終受入を進めている段階です。

## 現在できること

現在のmainでは、次が成立しています。

- Better Auth のCookieセッションでログイン
- `GET /api/v1/me` によるユーザー/ロール取得
- `RoleBookPermission` に基づく書籍一覧・詳細の認可
- 認可済みEPUB/PDFの保護配信（両方ある場合はEPUB優先）
- PDFページ移動、EPUB CFI位置移動
- 読書位置・状態のPostgreSQL保存・復元
- PDF最終ページ/EPUB最終locationでの`completed`保存とcompleted維持
- 未認証401 / 権限なし403
- タイトル/著者検索、backendのカテゴリ絞り込み、ユーザー別読書状態
- Admin UIからのEPUB/PDF・メタデータ・公開範囲の登録
- 保存済み書籍一覧、書籍情報/公開範囲編集、論理削除/復元
- カテゴリ管理、一般ユーザー登録・利用停止/再開・仮パスワード再設定
- 実PostgreSQL + Better Auth Cookie + Playwrightによる主要経路テスト

現在の保護ファイル保存はGit管理外のローカルストレージです。これはMVP縦切りの暫定実装であり、正式要件のCloudflare R2を置き換える決定ではありません。

## まだ未完成の主な項目

- Cloudflare R2保存とsigned URL / renewal semantics
- R2 object lifecycleを含む完全削除、ファイル差し替え、復旧
- Home画面のカテゴリ選択UI
- reader由来の最終的な進捗率表示 semantics
- PostgreSQL backup/restore acceptance
- PC Chrome / Android Chromeの正式な最終受入

詳細は `docs/current-status.md` と `docs/requirements.md` を参照してください。

## 技術スタック

### Frontend

- React 19 / TypeScript / Vite / React Router
- Tailwind CSS / shadcn/ui
- TanStack Query
- React Hook Form / Zod
- EmbedPDF / React Reader

### Backend

- Node.js / Hono / TypeScript
- Better Auth
- Prisma / PostgreSQL
- OpenAPI / Scalar

### Test / Development

- Vitest / Playwright
- Docker / Docker Compose / pgAdmin4

## Source of truth

- プロジェクトルール: `AGENTS.md`
- 正式要件: `docs/requirements.md`
- 現在状態: `docs/current-status.md`
- 実装済みHTTP契約: `docs/api.yaml`
- DB構造: `backend/prisma/schema.prisma`
- DB説明: `docs/database.md`
- MVP実行計画: `docs/mvp_plan.md`
- 画面方針: `docs/design.md`

DB構造は常に `schema.prisma` を優先します。

## ローカル開発

依存関係:

```bash
npm install
npm install --prefix backend
npm install --prefix frontend
```

開発サーバー:

```bash
npm run dev
```

- Frontend: `http://localhost:5173`
- Backend local: `http://localhost:3000`
- Backend Docker: `http://localhost:3001`

環境変数は各 `.env.example` を参照し、実値をGitへコミットしないでください。現在のローカルEPUB/PDF縦切りでは `BOOK_FILE_STORAGE_ROOT` を使用します。

## Fresh環境のセットアップ

READMEだけを入口にfresh環境を作る場合は、task-ownedのPostgreSQLを起動し、committed migration、Prisma Client、開発用seedを順に適用します。seed用パスワードはローカルで設定し、Gitへ保存しないでください。

```bash
cp .env.example .env.development
# .env.development の POSTGRES_PASSWORD、DATABASE_URL、BETTER_AUTH_SECRET、
# SEED_USER_PASSWORD、SEED_ADMIN_PASSWORD をローカル値へ設定する
docker compose --env-file .env.development -f docker-compose.dev.yml up -d db

npm install
npm install --prefix backend
npm install --prefix frontend

set -a
. ./.env.development
set +a

(cd backend && npx prisma generate && npx prisma migrate deploy && npx prisma db seed)
npm run dev
```

seedを使わず初期管理者だけを作る場合は、`INITIAL_ADMIN_EMAIL`、`INITIAL_ADMIN_NAME`、`INITIAL_ADMIN_PASSWORD` を設定して次を実行します。

```bash
npm --prefix backend run create:initial-admin
```

DBを破棄して再現する場合は、同じCompose projectで起動したtask-ownedリソースに対してだけ次を実行します。

```bash
docker compose --env-file .env.development -f docker-compose.dev.yml down -v
```

Prismaの現在schema確認:

```bash
cd backend
npx prisma validate
```

新規環境はcommitted migrationだけで再現できます。`backend` の `verify:file-hash-migration` は列契約、Prisma read/write、重複hash拒否を確認します。非空の既存DBへ適用する場合は、実ファイル内容からのhash backfill方針を先に確定してください。

## テスト / Build

```bash
npm test
npm run build:backend
npm run build:frontend
```

個別:

```bash
npm --prefix backend test
npm --prefix frontend test
npm --prefix frontend run test:e2e
```

`build:frontend` にはMVP外の既知TypeScriptエラーが残る可能性があります。失敗時は今回変更起因か既存課題かを分離して扱ってください。

## MVP PDF demo / browser E2E

seedのサンプル書籍はメタデータ中心のため、保護PDFを含む最短のdemoは既存のtask-owned E2E fixtureを使用します。`E2E_DATABASE_URL` は必ず `_e2e` で終わるDB名にし、実値は環境変数だけに設定してください。

```bash
mkdir -p .tmp/e2e-book-files
docker compose --env-file .env.development -f docker-compose.dev.yml exec -T db \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "CREATE DATABASE booklib_demo_e2e OWNER $POSTGRES_USER;"

export E2E_DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5432/booklib_demo_e2e"
export E2E_ADMIN_EMAIL="phase-c-admin@example.test"
export E2E_ADMIN_NAME="Phase C Admin"
export E2E_ADMIN_PASSWORD="replace-with-a-local-password"
export E2E_MVP_PASSWORD="replace-with-a-local-password"
export E2E_BOOK_FILE_STORAGE_ROOT="$(pwd)/.tmp/e2e-book-files"
export E2E_MVP_METADATA_PATH="$(pwd)/.tmp/e2e-metadata.json"

npm --prefix frontend run test:e2e
```

このdemoは、ログイン、閲覧可能な書籍一覧、保護PDF、ページ移動、読書位置保存、reload後の復元、権限なしユーザーの拒否を確認します。

同じtask-owned E2E DBとstorageでdemoを再実行する場合は、fixtureが既存roleとPDFを再利用しないため、先に次を実行します。

```bash
docker compose --env-file .env.development -f docker-compose.dev.yml exec -T db \
  psql -U "$POSTGRES_USER" -d postgres \
  -c 'DROP DATABASE IF EXISTS booklib_demo_e2e WITH (FORCE);' \
  -c "CREATE DATABASE booklib_demo_e2e OWNER $POSTGRES_USER;"
find .tmp/e2e-book-files -type f -delete
rm -f .tmp/e2e-metadata.json
```

## 現在のMVP完了判定

現在の「non-R2 core VERIFIED」は次の縦切りを意味します。

```text
login
→ permissioned book list
→ protected PDF/EPUB
→ page/location move
→ PostgreSQL save
→ reload
→ restore
```

これは `docs/requirements.md` の正式MVP全項目完了を意味しません。正式MVPは、R2、signed URL、ファイル lifecycle、進捗率、backup/restore、PC/Android受入を含む正式条件を満たした時点で完了とします。

## License

ISC
