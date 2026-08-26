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

### Admin registration

`/admin` の新規書籍登録sheetでは次を扱う。

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

現在の書籍一覧表示は、そのページを開いている間に登録成功した書籍を表示するfocused実装であり、reload後にDB上の全管理書籍を再取得する管理一覧は残タスク。

正式storage targetはCloudflare R2だが、現在の登録経路はprotected local storageを使用する。frontendはstorage pathへ依存しない。

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
