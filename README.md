# BeLib

BeLib は、EPUB/PDF をブラウザで管理・閲覧する個人向け Web アプリです。

## 技術スタック

- Frontend: React + TypeScript + Vite
- Backend: Hono + Prisma + PostgreSQL

## ディレクトリ構成

- `frontend`: フロントエンドアプリ
- `backend`: API サーバーと Prisma スキーマ
- `docs`: 要件・設計・DB定義・API仕様

## 開発環境の起動

リポジトリルートで実行します。

```bash
npm install
npm run dev
```

- Frontend: Vite 開発サーバー
- Backend: Hono 開発サーバー

## バックエンド単体での実行

```bash
cd backend
npm install
npm run dev
```

## よく使うコマンド（backend）

```bash
npm run build
npm run test
npx prisma format
npx prisma validate
npx prisma generate
```

## API ドキュメント

バックエンド起動後:

- OpenAPI JSON: `/doc`
- Scalar UI: `/scalar`

## BookFile の保存先について

BookFile はローカルファイルパスではなく、Cloudflare R2 の URL を保存します。
Prisma モデル `BookFile` では `fileUrl`（DB カラム `file_url`）を使用します。
