# BeLib Backend

Hono + Better Auth + Prisma + PostgreSQL でBeLibの認証、書籍認可、PDF配信、読書進捗、最小管理APIを提供します。

## 現在の主な責務

- Better Auth の認証ハンドラー
- `GET /api/v1/me`
- `RoleBookPermission` による書籍認可
- 書籍一覧・詳細
- 保護されたPDF配信
- `ReadingInfo` の取得・保存
- 管理者向け最小書籍登録
- 管理者向け最小PDF登録
- OpenAPI / Scalar

実装状態の詳細は `../docs/current-status.md`、現在のHTTP契約は `../docs/api.yaml` を参照してください。

## 開発

```bash
npm install
npm run dev
```

既定のローカルAPIは `http://localhost:3000` です。

## 主なスクリプト

```bash
npm run dev
npm run build
npm test
npm run create:initial-admin
npm run prepare:real-e2e-fixture
```

実行に必要な環境変数は `.env.example` を参照し、実値をGitへコミットしないでください。

## Protected PDF storage

現在のMVP縦切りでは `BOOK_FILE_STORAGE_ROOT` 配下のGit管理外ストレージを使用します。

重要:

- `BookFile.fileUrl` は現在、storage root配下の相対キーとして扱う
- ファイルをpublic directoryから直接配信しない
- file endpointで認証と`RoleBookPermission`を確認する
- path traversalやstorage root外への解決を拒否する
- 現在はPDFのみを実経路として扱う

これは正式なR2要件を廃止する設計変更ではありません。R2への移行後も、バックエンドでの認証・認可境界を維持します。

## Current versioned routes

```text
GET   /api/v1/me
GET   /api/v1/books
GET   /api/v1/books/:bookId
GET   /api/v1/books/:bookId/file
GET   /api/v1/books/:bookId/reading-info
PATCH /api/v1/books/:bookId/reading-info
POST  /api/v1/admin/books
POST  /api/v1/admin/books/:bookId/files
```

Better Auth自体は `/api/auth/*` で扱います。

## Prisma

DB構造の正本は `prisma/schema.prisma` です。

```bash
npx prisma validate
```

`BookFile.fileHash` を含む現在schemaとcommitted migrationの再現性には既知のdriftがあります。新規環境のmigration再現を正式完了条件として扱い、古いmigrationを黙って書き換えないでください。

## 初期管理者

初期管理者は `create:initial-admin` CLIで作成します。認証情報はGit管理外の環境変数として与え、引数やドキュメントへ実値を書かないでください。

CLIは既存管理者を重複作成せず、一般ユーザーと同じメールアドレスが存在する場合は管理者への昇格を拒否する設計です。

## Test boundary

backendの変更後は、まず対象testを実行し、その後必要に応じてfull testとbuildを実行します。

```bash
npm test
npm run build
```

DB schemaを変更した場合はPrisma validate/formatとmigration整合も確認してください。
