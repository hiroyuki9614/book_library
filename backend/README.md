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
npm run verify:r2
```

実行に必要な環境変数は `.env.example` を参照し、実値をGitへコミットしないでください。

## Book file storage

`BOOK_FILE_STORAGE_DRIVER` で保存先を切り替えます。未指定時は `local` です。

- `local`: `BOOK_FILE_STORAGE_ROOT` 配下へ保存し、認可後にbackendからstreamする
- `r2`: Cloudflare R2へ保存し、認可後に1時間有効のGET署名URLへ302 redirectする

`BookFile.fileUrl` は公開URLではなくstorage keyです。R2では `books/<uuid>.<ext>`、localでは `<uuid>.<ext>` を保持します。管理登録でR2/local保存後にDB作成が失敗した場合、保存済みobject/fileの補償削除を試み、削除失敗はログへ残します。

重要:

- ファイルをpublic directoryへ直接配置しない
- file endpointで認証と`RoleBookPermission`を確認してから取得手段を発行する
- local driverではpath traversalやstorage root外への解決を拒否する
- R2のブラウザ取得にはbucket CORSが必要
- 既存local DBの `fileUrl` を移行せずdriverだけR2へ変更しない
- live R2 smoke / browser CORS / production cutoverは `docs/runbooks/r2.md` に従う

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

`BookFile.fileHash` は `20260816140000_add_book_file_hash` のforward migrationで追加されます。既存migrationは書き換えず、新規環境では次の順でmigrationと契約を確認できます。

```bash
npx prisma migrate deploy
MIGRATION_TEST_DATABASE_URL="$DATABASE_URL" npm run verify:file-hash-migration
```

既に `book_files` の行があるDBへ適用する場合、実ファイル内容からのhash backfill方針を先に確定してください。このmigrationはそのbackfillやproduction/shared DBへのwriteを行わないため、方針未確定の非空DBへそのまま適用してはいけません。

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
