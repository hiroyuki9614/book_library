# BeLib Current Implementation Status

- Updated: 2026-08-26
- Inspected main: `075b9d4118f89f598c13104cf61f013ad48a9521`
- Purpose: 現在の実装事実、部分実装、正式要件との差分、次の作業境界を記録する

## Source-of-truth boundary

```text
current user request
> AGENTS.md
> backend/prisma/schema.prisma
> docs/requirements.md
> current implementation + tests
> docs/api.yaml
> this status document
```

`docs/requirements.md` は正式要件、`backend/prisma/schema.prisma` は現在のDB構造です。保護ローカル保存は暫定実装であり、Cloudflare R2要件を置き換えません。

## Implemented

### Authentication and user/session behavior

- Better AuthのCookie sessionを使用しています。
- `GET /api/v1/me` は認証済みユーザーのID、表示名、メールアドレス、ロールを返します。
- 利用停止ユーザーの新規メールログインは401で拒否します。
- 既に確立したsessionは、ユーザーの`deletedAt`に関係なく有効期限まで`/me`、書籍閲覧、読書情報を利用できます。
- 管理者は一般ユーザーを登録、利用停止、利用再開できます。
- 管理者は一般ユーザーのcredential accountの仮パスワードを再設定できます。既存sessionは削除しません。

### Book viewing and reading state

- `RoleBookPermission` により許可された、論理削除されていない書籍だけを一覧・詳細・ファイル・読書情報の対象にします。
- 書籍一覧はページング、タイトル/著者の大文字小文字を区別しない検索、`categoryId`絞り込みに対応します。
- 一覧・詳細の`readStatus`は現在ユーザー自身の`ReadingInfo`から返します。
- 認可済みPDF/EPUBをbackend経由で保護配信します。EPUBとPDFの両方がある場合はEPUBを優先します。
- PDFページ位置とEPUB CFIを`ReadingInfo.currentPosition`へ保存・復元できます。
- PDFの最終ページ、EPUBの最終locationで`completed`を保存します。
- 一度`completed`になった読書状態は、途中位置の保存で`reading`へ戻しません。
- Reader画面は書籍のファイル形式に応じてPDF/EPUB readerへ分岐します。

### Admin book and category management

- EPUB/PDFの拡張子、MIME、内容、200MB上限を検証して、メタデータとファイルを一括登録できます。
- SHA-256の`BookFile.fileHash`で、論理削除済み書籍を含む同一ファイルの重複登録を拒否します。
- 登録時の公開範囲は`all_users`または`admin_only`から明示選択し、role permissionsへ保存します。
- `/admin`はDBから保存済み書籍一覧を再取得し、カテゴリ、公開範囲、ファイル概要を表示します。
- 書籍メタデータと公開範囲の編集、論理削除、削除済み一覧、復元に対応します。メタデータ/公開範囲の更新では`ReadingInfo`を変更しません。
- 管理者限定から全ユーザー公開へ変更する場合、Admin UIで確認ダイアログを表示します。
- カテゴリの一覧、追加、名称変更、inactive化に対応します。使用中カテゴリの書籍は固定の「未分類」へ移動します。
- `docs/api.yaml`に記載する管理API以外にも、旧vertical slice互換としてmetadata-onlyの`POST /api/v1/admin/books`を保持しています。新規Admin UIはfull registrationを使用します。

## Partially implemented

- ファイルは`BOOK_FILE_STORAGE_ROOT`配下のprotected local storageへ保存し、認可済みbackendがstreamします。Cloudflare R2への保存切替、signed URL、期限更新、production cutoverは未実装です。
- 現在の登録経路はMVPで1冊1ファイルを強制します。既存ファイルの差し替え、復旧、R2 object lifecycleは未実装です。
- 書籍のタイトル/著者検索はbackend APIに実装済みですが、Home画面は取得済み一覧を`BookTable`内でローカル絞り込みしています。
- `categoryId`のbackend絞り込みは実装済みですが、Home画面にカテゴリ選択UIはありません。
- Homeの読書状態集計とstatus filterはbackendの`readStatus`を使います。一方、平均進捗率は既存prototype/mockデータに依存しており、reader由来の最終的な百分率 semanticsは未確定です。
- EPUB/PDFの登録、管理、読書情報のfocused testとbuildはCIで確認していますが、PC ChromeとAndroid Chromeの正式な最終受入は未完了です。

## Still open

### R2 and file lifecycle

- Cloudflare R2へのEPUB/PDF保存と正式なstorage adapter切替
- 1時間のsigned URL、期限前の自動再発行、再発行時の認可境界に関する正式要件の実装
- R2保存後のDB失敗時補償、完全削除、ファイル差し替え、復旧

### Formal MVP acceptance

- 完全削除と、それに伴うR2ファイル・DBの整合性受入
- readerの位置から算出する最終的な進捗率表示 semantics
- PostgreSQL backup/restore acceptance
- PC ChromeおよびAndroid Chromeの正式な正常系受入

## Database / Prisma

DB構造の正本は`backend/prisma/schema.prisma`です。現在の主要制約は次のとおりです。

- `BookFile.fileHash`はuniqueです。
- `ReadingInfo`は`userId + bookId`でuniqueです。
- `RoleBookPermission`は`roleId + bookId`でuniqueです。
- `ReadingInfo.currentPosition`はnullableな`VARCHAR(255)`で、PDFページ文字列またはEPUB CFIを保持できます。
- `ReadingInfo.readStatus`のdefaultは`unread`です。
- `Book.pageTurnDirection`のdefaultは`ltr`です。

`docs/database.md`はこのschemaの説明であり、型・nullable・default・relationはschemaを優先します。今回の実装確認では文書とschemaのmaterialな矛盾は確認されませんでした。

## Verification basis

関連テストは、認証/session、書籍認可・保護配信、EPUB優先、検索/カテゴリ、ReadingInfo、管理書籍、カテゴリ、一般ユーザー、frontend Admin/Readerを対象にしています。対応するCI workflowは次の4つです。

- `.github/workflows/non-r2-core-ci.yml`
- `.github/workflows/disabled-user-session-ci.yml`
- `.github/workflows/admin-registration-ci.yml`
- `.github/workflows/epub-reader-ci.yml`

これらは実装範囲の回帰ゲートであり、R2切替、完全削除、backup/restore、PC/Android最終受入の完了を意味しません。

## Documentation responsibilities

- `docs/requirements.md`: 正式なtarget behavior。実装未完了を理由に書き換えない
- `backend/prisma/schema.prisma`: 現在のDB構造
- `docs/api.yaml`: 現在実装済みのversioned HTTP contract
- `docs/current-status.md`: 現在の実装とdrift
- `docs/mvp_plan.md`: 実行順と完了gate
- Git履歴: 同期・検証の実行証跡
