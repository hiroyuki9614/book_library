# 1. プロジェクト名

BeLib

## 2. アプリ概要

BeLib は、電子書籍の閲覧体験と書籍メタデータ管理を同じ画面遷移上で扱うことを目的にしたアプリです。  
現状は MVP 段階で、フロントエンド中心に読書画面と管理画面の基盤を実装しています。

## 3. 制作背景・目的

- EPUB/PDF の閲覧と管理が別アプリに分かれがちな課題を、単一アプリで扱いたかった
- 読書位置保存やカテゴリ管理を含む「自分用ライブラリ」の体験を検証したかった
- React + Hono + Prisma を使ったフルスタック構成の設計力を示すポートフォリオを作りたかった

## 4. 主な機能（実装済み）

- ログイン画面とルートガード
  - `GuestRoute` / `RequireAuth` を実装
  - better-authのCookieセッションと`GET /api/v1/me`から認証ユーザーとロールを取得
- 書籍一覧画面
  - 検索、ステータス絞り込み、並び替え UI
  - 読書サマリー表示（総冊数、読書中、読了、未読、平均進捗）
- EPUB リーダー
  - ページ移動、目次ジャンプ、進捗表示
  - 読書位置を localStorage に保存（仮実装）
- PDF リーダー
  - ページ移動、ズーム、表示制御
- 管理画面
  - 書籍登録フォーム（React Hook Form + Zod）
  - 画面内状態への登録反映（仮実装）
- バックエンド基盤
  - Hono API (`/health`, `/test`, `/api/auth/*`)
  - OpenAPI/Scalar (`/doc`, `/scalar`)
  - Prisma スキーマ（ユーザー、ロール、書籍、書籍ファイル、読書情報など）

## 5. スクリーンショット

現時点では README 掲載用に整理した画面キャプチャが未配置のため、追加予定です。

- ログイン画面
- 書籍一覧画面
- EPUB/PDF リーダー画面
- 管理画面

## 6. 使用技術

### フロントエンド

- React 19
- TypeScript
- Vite
- React Router
- Tailwind CSS
- shadcn/ui
- React Hook Form
- Zod
- TanStack Query
- epubjs
- @embedpdf 系プラグイン

### バックエンド

- Node.js
- Hono
- TypeScript
- Prisma
- PostgreSQL
- better-auth

### 開発・インフラ

- Docker
- Docker Compose
- pgAdmin4
- Vitest

## 7. システム構成

```mermaid
flowchart LR
 Browser[Browser]
 FE[Frontend: Vite React]
 BE[Backend: Hono API]
 DB[(PostgreSQL)]
 PGA[pgAdmin4]

 Browser --> FE
 FE --> BE
 BE --> DB
 PGA --> DB
```

開発時の主なポート:

- Frontend: `5173` (Vite デフォルト)
- Backend API: `3000`
- PostgreSQL: `5432`
- pgAdmin: `8080`

参照仕様書:

- 要件定義: `docs/requirements.md`
- 画面設計: `docs/design.md`
- データベース定義: `docs/database.md`
- API 仕様: `docs/api.yaml`
- 開発ガイドライン: `docs/guidline.md`
- プロジェクト運用ルール: `AGENTS.md`

## 8. 技術的に工夫した点

- フロントエンド/バックエンドを別パッケージで分離し、責務を明確化
- Hono + OpenAPI で API 仕様をコードから確認しやすい構成にした
- Prisma スキーマで権限や読書状態のリレーションを先に定義し、DB設計を先行
- `BookFile.fileUrl` を採用し、ローカルパス依存を避けたストレージ抽象化を意識
- React Hook Form + Zod によりフォーム入力の型安全性とバリデーションを両立
- Docker Compose で API / DB / pgAdmin を一括起動できる開発環境を用意

## 9. 苦労した点・課題

- 認証基盤（better-auth）導入と既存ロール設計の整合
- EPUB と PDF でライブラリが異なるため、操作体験の統一設計に工夫が必要
- 現在は UI 先行のため、一部がモックデータやローカル状態に依存している

## 10. ローカル環境での起動方法

必要ソフトウェア:

- Node.js 22 以上
- npm
- Docker / Docker Compose

手順:

1. 環境変数ファイルを作成（リポジトリルート: `.env.development`）

```env
POSTGRES_USER=user
POSTGRES_PASSWORD=your_password
POSTGRES_DB=app_db
DATABASE_URL=postgresql://user:your_password@localhost:5432/app_db?schema=public
NODE_ENV=development
```

バックエンドとフロントエンドの環境変数ファイルを作成する。

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

- `VITE_API_BASE_URL` はブラウザから接続するバックエンドURLで、ローカル開発の既定値は `http://localhost:3000`
- フロントエンドの標準ポートは `5173`、バックエンドの標準ポートは `3000`
- Cookieセッションを使用する認証リクエストでは、Fetch APIの`credentials`を有効にする必要がある
- `frontend/.env` はローカル設定のためGit管理せず、設定例の`frontend/.env.example`だけをコミットする

1. DB と pgAdmin を起動

```bash
docker compose -f docker-compose.dev.yml --env-file .env.development up -d db pgadmin4 app
```

1. 依存関係をインストール

```bash
npm install
npm install --prefix backend
npm install --prefix frontend
```

1. Prisma マイグレーションを適用

```bash
cd backend
npx prisma migrate dev
```

1. 開発サーバーを起動（リポジトリルート）

```bash
npm run dev
```

アクセス先:

- Frontend: `http://localhost:5173`
- Backend: `http://localhost:3000`
- OpenAPI: `http://localhost:3000/doc`
- Scalar: `http://localhost:3000/scalar`
- pgAdmin: `http://localhost:8080`

## 11. 今後の予定

- 書籍・カテゴリ・ユーザー管理 API の本実装
- 管理画面登録処理の DB 永続化
- 読書位置保存のサーバー連携（localStorage 仮実装から移行）
- ロールベース権限管理のエンドツーエンド実装
- オブジェクトストレージ連携（Cloudflare R2 への実アップロード）
- README 向けスクリーンショット整備

## 12. ライセンス

ISC
