# BeLib

BeLib は、認証・ロール別閲覧権限・読書進捗を備えた個人向け EPUB / PDF ライブラリを目指すフルスタックWebアプリです。

現在は **PDFのMVPコア縦切りを実装・実runtime検証済み** で、正式MVPに向けてストレージ、公開範囲、EPUB、管理機能を仕上げている段階です。

## 現在できること

実装checkpoint `checkpoint/belib-mvp-phase6-20260812` では、次が成立しています。

- Better Auth のCookieセッションでログイン
- `GET /api/v1/me` によるユーザー/ロール取得
- `RoleBookPermission` に基づく書籍一覧・詳細の認可
- 認可済みPDFの保護配信
- PDFページ移動
- 読書位置のPostgreSQL保存・復元
- 未認証401 / 権限なし403
- 管理者向け最小書籍メタデータ登録API
- 管理者向け最小PDF登録API
- 実PostgreSQL + Better Auth Cookie + Playwrightによる主要経路E2E

現在の保護ファイル保存はGit管理外のローカルストレージです。これはMVP縦切りの暫定実装であり、正式要件のCloudflare R2を置き換える決定ではありません。

## まだ未完成の主な項目

- Cloudflare R2保存と正式な署名URL運用
- EPUBの保護配信・サーバー進捗連携
- 管理画面UIから実admin APIへの登録接続
- 書籍ごとの公開範囲選択（全ユーザー / 管理者のみ）
- `completed` の自動遷移
- 検索・カテゴリ絞り込み
- ユーザー管理
- 論理削除・復元・完全削除
- ファイル差し替え
- PostgreSQL migration/schema driftの解消
- fresh環境でREADME手順を再現する最終確認

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

環境変数は各 `.env.example` を参照し、実値をGitへコミットしないでください。現在のローカルPDF縦切りでは `BOOK_FILE_STORAGE_ROOT` を使用します。

Prismaの現在schema確認:

```bash
cd backend
npx prisma validate
```

新規環境をmigrationだけで再現できることの最終確認はPhase 6の残作業です。

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

## 現在のMVP完了判定

現在の「MVPコア VERIFIED」は次の縦切りを意味します。

```text
login
→ permissioned book list
→ protected PDF
→ page move
→ PostgreSQL save
→ reload
→ restore
```

これは `docs/requirements.md` の正式MVP全項目完了を意味しません。正式MVPは、残るストレージ・公開範囲・管理・EPUB等の受け入れ条件を満たした時点で完了とします。

## License

ISC
