# 画面定義書

- 最終更新: 2026-10-31
- 実routeの正本: `frontend/src/routes/AppRoutes.tsx`

この文書は画面の役割と共通デザイン方針を説明します。route自体はfrontend実装を優先します。

## 共通デザイン

- 背景色: 白 (`#fff`)
- 文字色: 濃紺 (`--foreground`)
- サイドナビ: `#1F293D`
- サイドナビ文字: `#FDFDFD`
- modal / sheet背景: 白
- accent / secondary: 淡い青みの色 (`--accent`, `--secondary`)
- UI基盤: shadcn/ui + Tailwind CSS

既存componentやdesign tokenが実装済みの場合、デザイン変更依頼なしに全面変更しません。

### 2026-10-31 採用デザイン: Paper & Navy（実装候補。未リリース）

- EPUBのヘッダー、本文、進捗表示、操作部は白を基調にし、濃紺を主要な文字・操作色にする。
- 本文表示領域はreader幅100%。外側の紙風余白、角丸、影を付けない。
- EPUB iframe内の本文左右だけ各16px空ける。書籍タイトルと既存の目次・表示設定・移動メニューは維持する。
- この採用要件は `docs/current-status.md` の2026-10-31候補記録に対応する。本番適用の可否は同記録のrelease statusを参照する。
- ブラウザータイトルとブランド表記は `BeLib`。

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

### Reader

`/reader/:id` は書籍詳細APIの `fileType` と `pageTurnDirection` を使ってEPUB/PDF readerを選択する。

- EPUBはepub.jsの階層目次、CFI、generated locationsを使用する。位置は1始まりで表示し、算出不能時は不正な数値を表示しない。
- PDFはEmbedPDFのbookmark/scroll stateを使用し、アウトラインと固定ページ由来の進捗を表示する。
- 目次は右側sheetに表示し、長い内容だけを内部スクロールさせる。本文領域の高さは維持する。
- 本文左右端約15%の軽いタップでページを送る。中央、リンク、入力・ボタン、文字選択、ドラッグ、スクロール、ピンチ操作、目次表示中はページ送り対象外とする。
- Androidの狭い画面では操作列を折り返し、本文領域を不必要に固定縮小しない。

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
