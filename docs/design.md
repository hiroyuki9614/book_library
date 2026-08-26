# 画面定義書

- 最終更新: 2026-08-23
- 実routeの正本: `frontend/src/routes/AppRoutes.tsx`

この文書は画面の役割と共通デザイン方針を説明します。route自体はfrontend実装を優先します。

## 共通デザイン

- 背景色: `#EEECDC`
- 文字色: `#282828`
- サイドナビ: `#1F293D`
- サイドナビ文字: `#FDFDFD`
- modal背景: `#FDFDFD`
- overlay: `#D9D9D9`
- UI基盤: shadcn/ui + Tailwind CSS

既存componentやdesign tokenが実装済みの場合、デザイン変更依頼なしに全面変更しません。

## 現在の画面

| route | guard | 役割 |
| --- | --- | --- |
| `/login` | guest only | ログイン |
| `/` | authenticated | Home。書籍一覧の入口 |
| `/reader/:id` | authenticated | 保護されたEPUB/PDF Reader |
| `/admin` | authenticated + admin | 書籍管理UI。activeカテゴリ・公開範囲・EPUB/PDFを選択し、backendのfull registration APIへ実登録 |
| `/about` | authenticated | About |
| `/contact` | authenticated | Contact |
| `*` | authenticated layout内 | 404 |

旧文書にあった「ダッシュボード」「書籍一覧画面」「マイページ」という独立画面前提は、現在のrouterと一致しないため削除します。

### Admin book management

`/admin` では次の書籍管理を行う。

- 保存済み書籍一覧の取得（通常書籍 / 削除済み）
- 書籍情報と公開範囲の編集
- 通常書籍の論理削除、削除済み書籍の復元
- 管理者限定から全ユーザー公開へ変更する場合の確認

新規書籍登録sheetでは次を扱う。

- タイトル
- 著者
- backendから取得したactiveカテゴリ
- ページ方向
- 出版社
- 出版日
- 説明
- 公開範囲（全ユーザー公開 / 管理者のみ。初期値なし）
- EPUB/PDFファイル（最大200MB）

送信成功はbackendのfull registration APIがBook・BookFile・RoleBookPermissionの登録を返した後だけ表示する。

書籍一覧はページ表示時に`GET /api/v1/admin/books`からDBの保存済み書籍を取得し、reload後も維持されます。削除済み一覧ではファイルと読書記録を保持したまま復元できます。

同じ画面でカテゴリの追加、通常カテゴリの名称変更、カテゴリのinactive化を行えます。使用中カテゴリを削除すると、backendが書籍を固定の「未分類」へ移動します。

### Admin user management

`/admin/users` では一般ユーザーの一覧、登録、利用停止、利用再開、仮パスワード再設定を行います。利用停止は`deletedAt`で表現し、既存sessionと読書記録は保持します。利用停止ユーザーの新規ログインはbackendで拒否します。

### Home and Reader

Homeは認可済み書籍一覧を取得し、取得済みデータに対してタイトル/著者検索と読書状態filterを表示します。カテゴリ絞り込みはbackend APIに実装済みですが、Homeのカテゴリ選択UIは未接続です。平均進捗率はreader由来の最終表示仕様が未確定です。

Readerは書籍の`fileType`に応じてPDFまたはEPUBへ分岐し、保護file endpointから取得したBlobを表示します。PDFページまたはEPUB CFIを`ReadingInfo`へ保存・復元します。

正式storage targetはCloudflare R2ですが、現在の登録・配信経路は`BOOK_FILE_STORAGE_ROOT`配下のprotected local storageを使用します。frontendはstorage pathへ依存しません。R2、signed URL、期限更新、完全削除、ファイル差し替えは未実装です。

## 主なUI部品

- side navigation
- button
- card
- modal / sheet
- form
- table
- dropdown menu
- loading / skeleton
- Reader controls

## Figma

非公開Figmaは視覚設計の補助資料です。現在routeや実装状態の正本としては扱いません。
