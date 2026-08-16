# BeLib Frontend

React + TypeScript + Vite でBeLibのログイン、書籍一覧、PDF/EPUB Reader、管理画面UIを提供します。

## 現在の実装状態

### 実APIへ接続済み

- セッションベースの認証フロー
- `GET /api/v1/me` に基づく認証状態
- 書籍一覧
- 書籍詳細
- 認可済みPDF取得
- PDF読書位置の取得・保存・復元
- Readerの認証保護
- 管理者画面のルート保護

### まだ仮実装 / 未接続

- 管理画面の書籍一覧と登録フォームは `booksData` とlocal component stateを使用
- 管理画面UIはbackendのadmin registration APIへ未接続
- EPUB Readerは存在するが、現在の保護backend/storage/progress縦切りはPDF中心
- 一覧UIには検索・絞り込みがあるが、backendの正式な検索/filter contractは未完成

詳細は `../docs/current-status.md` を参照してください。

## 主なルート

```text
/login       guest only
/            authenticated
/reader/:id  authenticated
/admin       authenticated + admin
/about       authenticated
/contact     authenticated
```

## API layer

主な本番経路:

- `src/api/books.ts`
  - `fetchBooks`
  - `fetchBook`
  - `fetchBookFile`
- `src/api/readingInfo.ts`
  - `fetchReadingInfo`
  - `saveReadingInfo`

PDF取得ではresponseのContent-Typeを確認し、非PDFレスポンスをReaderへ渡さないようにします。

## 開発・検証

利用可能なscriptは `package.json` を正本として確認してください。

現在は `dev`、`test`、`lint`、`build`、`test:e2e` が定義されています。Playwrightのreal MVP vertical testは、実PostgreSQL・認証済みbrowser・保護PDF・読書位置保存/復元の縦切りを検証します。

現在のcheckpointではMVP PDF導線とは別の既知TypeScriptエラーがfrontend full buildに残るため、build failureは変更起因か既存課題かを分離して扱ってください。

## UI / design

UIの正本補助は `../docs/design.md`、正式要件は `../docs/requirements.md` です。デザイン変更が目的でない作業では既存レイアウトを不要に変更しないでください。
