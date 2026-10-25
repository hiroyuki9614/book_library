# BeLib

BeLib は、認証・ロール別閲覧権限・読書進捗を備えた個人向け EPUB / PDF ライブラリです。管理者の個人蔵書と、一般ユーザーへ公開するデモ書籍を同じライブラリ内で権限分離します。

## 現在できること

- Better Auth のCookieセッションでログイン
- `GET /api/v1/me` によるユーザー/ロール取得
- `RoleBookPermission` に基づく一覧・検索・詳細・ファイル・読書位置の認可
- EPUB/PDFの保護配信（local stream / R2署名URL）
- PDFページとEPUB CFIのPostgreSQL保存・復元
- 未認証401 / 権限なし403
- 管理者向けEPUB/PDF登録、明示的な全ユーザー公開 / 管理者限定設定
- 管理者向け書籍・カテゴリ・一般ユーザー管理の主要経路
- Vitestと実PostgreSQL + Better Auth Cookie + Playwrightによる認証・認可・読書経路E2E

書籍ファイル保存はstorage driver経由です。ローカル開発/E2Eでは明示的にlocal storageを使用し、production候補では `BOOK_FILE_STORAGE_DRIVER=r2` によりCloudflare R2へ保存できます。R2閲覧はbackendで認証・権限確認後、1時間有効の署名URL情報を返し、frontendがcredentialsなしで取得します。本番切替とブラウザCORS検証は運用承認が必要です。

## ポートフォリオデモ

ログイン画面を採用面接向けの入口として整え、一般ユーザーには公開デモ作品だけを表示します。公開サインアップは無効です。デモアカウントは秘密管理された環境変数から運用コマンドで作成し、資格情報をGitへ保存しません。共有アカウントでは読書位置も共有されることを画面に明示しています。

既存蔵書のadmin限定化、青空文庫の出典確認・EPUB/PDF生成・保護Admin API登録、本番前の停止条件は [Portfolio demo runbook](docs/portfolio-demo.md) を参照してください。本番DB・本番R2・デプロイを変更せずに、read-only preflightとdry-runまで実施できます。

![BeLib portfolio demo login](docs/images/portfolio-demo-login.jpg)

![General-user shelf containing public demo books](docs/images/portfolio-demo-shelf.jpg)

## まだ未完成の主な項目

- Cloudflare R2のlive credential/CORS/production cutoverと閲覧中URL自動再発行
- ポートフォリオデモの本番read-only preflight、バックアップ/復元証拠、承認付き権限切替
- 青空文庫生成物の目視確認と本番R2への承認付き登録
- 本番R2 CORSを含むEPUB/PDFブラウザE2E
- 完全削除
- ファイル差し替え
- 閲覧中のR2署名URL自動再発行（現在は読み込み時にBlob化）

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

環境変数は各 `.env.example` を参照し、実値をGitへコミットしないでください。local driverでは `BOOK_FILE_STORAGE_ROOT`、R2 driverでは `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET_NAME` を使用します。

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

## Portfolio EPUB/PDF demo / browser E2E

E2Eは、一般ユーザーへ公開するEPUB/PDF 2冊とadmin限定の既存蔵書相当1冊をtask-owned DB/storageへ作成します。`E2E_DATABASE_URL` は必ず `_e2e` で終わるDB名にし、実値は環境変数だけに設定してください。

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
# 生成済みの青空文庫2冊でreaderも確認する場合のみ指定
export E2E_AOZORA_MANIFEST_PATH="$(pwd)/.tmp/aozora-demo/manifest.generated.json"

npm --prefix frontend run test:e2e
```

このdemoは、一般ユーザーが公開2冊だけを一覧・検索できること、admin限定本の詳細・ファイル・進捗GET/PATCHと管理APIを拒否されること、adminは全冊を見られること、保護PDFページとEPUB CFIを保存・復元できることを確認します。`E2E_CAPTURE_PORTFOLIO_SCREENSHOTS=true` を指定した実行では、非公開書籍を含まないログイン/本棚画像を `docs/images/` へ更新します。

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

現在の「MVPコア VERIFIED」は次の縦切りを意味します。

```text
login
→ permissioned book list
→ role-filtered EPUB/PDF shelf
→ protected reader
→ PDF page / EPUB CFI move
→ PostgreSQL save
→ reload
→ restore
```

これは `docs/requirements.md` の正式MVP全項目完了を意味しません。正式MVPは、残るストレージ・公開範囲・管理・EPUB等の受け入れ条件を満たした時点で完了とします。

## License

ISC
