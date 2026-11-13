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

ログイン画面には管理者・一般ユーザーのデモ用ID/PW欄と、フォームへ入力するボタンがあります。デモ用の一般ユーザーには公開作品だけを表示します。公開サインアップは無効です。

公開する資格情報は、この用途で新しく作成したデモアカウント専用です。既存管理者や個人のパスワードを流用せず、資格情報はGitへ保存しません。一般ユーザー用は `VITE_PUBLIC_DEMO_EMAIL` / `VITE_PUBLIC_DEMO_PASSWORD`、管理者用は `VITE_PUBLIC_DEMO_ADMIN_EMAIL` / `VITE_PUBLIC_DEMO_ADMIN_PASSWORD` を使い、各ペアが両方揃った場合のみ表示します。管理者デモは通常の管理者権限を持ちます。

本番CDでは既定で公開値を空にし、所有者が明示した公開専用設定と、同じ資格情報による実ログイン・ロール確認を通った場合だけビルドへ渡します。共有アカウントでは読書位置も共有されます。運用手順は [Portfolio demo runbook](docs/portfolio-demo.md) を参照してください。

![BeLib portfolio demo login](docs/images/portfolio-demo-login.jpg)

![General-user shelf containing public demo books](docs/images/portfolio-demo-shelf.jpg)

## まだ未完成の主な項目

- Cloudflare R2のlive credential/CORS/production cutoverと閲覧中URL自動再発行
- ポートフォリオデモの本番read-only preflight、バックアップ/復元証拠、承認付き権限切替
- 青空文庫生成物の目視確認と本番R2への承認付き登録
- 本番R2 CORSを含むEPUB/PDFブラウザE2E
- 削除候補の本番反映・本番i5受入（完全削除の実装と隔離環境テストは追加済み）
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
```

## Portfolio EPUB/PDF demo / browser E2E

このE2Eは既存の release-ci.yml reader-e2e jobで実行する前提です。preflightはjob名等の環境値・workspace・localhostの reader_e2e DB設定を照合し、通常のローカル実行は接続前に拒否します。DB接続後はidentityと空状態もread-onlyで確認します。この確認はrunnerの真正性を認証するものではなく、手動で環境値を模倣した実行を防ぐセキュリティ境界ではありません。E2Eは既存のGitHub Actions workflowから実行してください。ローカル用DBの作成・削除手順はありません。

CI jobはtask-ownedの一時DB/storageへ一般ユーザー向けEPUB/PDF 2冊とadmin限定本1冊を作成し、公開範囲、権限、保護PDFページ、EPUB CFI、読書位置の保存と復元を検証します.
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
