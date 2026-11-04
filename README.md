# BeLib — ブラウザで読む、自分の本棚

**React / TypeScript × Hono × PostgreSQL で開発した、EPUB・PDFの蔵書管理・読書アプリです。**

書籍を登録して終わりではなく、**「ログイン → 権限に応じた本棚 → 本文を読む → 読んだ位置を保存・復元する」** までを一連の体験として実装しています。個人開発のポートフォリオとして、画面だけでなく、API・データベース・認証／認可・テスト・デプロイにも取り組んでいます。

**[Webデモ](https://belib.hiroyuki9614.com/login)** ｜ [実装状況](docs/current-status.md) ｜ [画面・操作の設計](docs/design.md)

> **公開環境について（2026-11-04）:** 本番サイトでHTTP 502の障害が観測され、復旧・受入確認中です。アクセスできない場合は、以下の画面イメージとコードをご覧ください。公開デモの稼働・認証付き読書動作を、現在の本番で検証済みとは表明していません。

## まずは画面をご覧ください

| ログイン画面 | 一般ユーザーの本棚 |
| --- | --- |
| ![BeLibのログイン画面](docs/images/portfolio-demo-login.jpg) | ![公開が許可された書籍だけを並べた本棚](docs/images/portfolio-demo-shelf.jpg) |

一般ユーザーには閲覧を許可したデモ書籍のみを表示し、管理者専用の蔵書は分離する設計です。公開サインアップは無効化しています。デモ用ログイン情報は**公開してよい一般ユーザー専用の情報を検証できた場合だけ**表示する仕組みで、管理者アカウントや非公開蔵書の情報は掲載しません。

## このアプリで実装したこと

| 領域 | 主な実装 | 工夫した点 |
| --- | --- | --- |
| 書籍管理 | EPUB/PDFの登録、書籍一覧・検索・詳細、カテゴリ、管理画面 | 一般ユーザーと管理者で操作可能な範囲を分ける |
| 認証・認可 | Better AuthによるCookieセッション、ロール別の書籍閲覧権限 | **画面の非表示だけに頼らずAPI側でも認可**。未認証401・権限不足403を区別 |
| ブラウザ読書 | EPUB/PDF表示、ページ操作、読書状態 | PDFのページ番号とEPUBのCFIを共通の読書進捗モデルで扱う |
| データ永続化 | Prisma / PostgreSQL | ユーザー×書籍ごとに進捗を保存し、再読み込み時に復元 |
| ファイル配信 | ローカル保存・Cloudflare R2対応 | APIで認証・権限確認してから保護ファイルを配信し、R2では期限付き署名URLを発行 |
| 品質確認 | Vitest / Playwright / 実PostgreSQLを使うE2E | ログイン、ロール別のアクセス制御、EPUB/PDF読書、進捗保存を検証 |

### 技術面で特に意識した3点

1. **フロントエンドとAPIの境界を明確にすること。** React側で見せないだけでは書籍ファイルを守れないため、一覧・詳細・ファイル取得・読書進捗の各APIで権限を判定しています。
2. **読書体験の状態を永続化すること。** PDFとEPUBで異なる読書位置をPostgreSQLに保持し、再表示・再ログイン後の復元を考慮しています。
3. **動くだけで終わらないこと。** 認証・認可の自動テスト、Docker Composeによる構成、CI、限定的なデプロイ・ロールバック手順を整備しています。本番受入は別途証拠を揃える運用です。

## アプリの構成

```text
ブラウザ（React / TypeScript / Vite）
  ├─ ログイン・書籍一覧・管理画面・EPUB/PDFリーダー
  └─ HTTP / Cookie セッション
       ↓
API（Node.js / Hono / Better Auth）
  ├─ 認証・ロール別認可
  ├─ 書籍・カテゴリ・読書進捗
  ├─ Prisma → PostgreSQL
  └─ 保護された書籍ファイル → local storage / Cloudflare R2
```

| 区分 | 使用技術 |
| --- | --- |
| Frontend | React 19、TypeScript、Vite、React Router、Tailwind CSS、shadcn/ui、TanStack Query |
| Reader | React Reader（EPUB）、EmbedPDF（PDF） |
| Backend | Node.js、Hono、TypeScript、Better Auth、Prisma |
| Database / Storage | PostgreSQL、Cloudflare R2（またはローカルストレージ） |
| Test / Infrastructure | Vitest、Playwright、Docker、Docker Compose、GitHub Actions |
| API仕様 | OpenAPI（[API定義](docs/api.yaml)） |

## 現在の完成範囲と残っている課題

**MVPの中核動作**として、ログイン、権限付き本棚、EPUB/PDFリーダー、読書位置の保存・復元を実装し、自動テストで検証しています。**これは本番運用を含むすべての要件が完了したという意味ではありません。**

継続課題には、R2署名URLの閲覧中の再発行、ファイル差し替え・完全削除、本番ブラウザでの最終受入、CDを含む運用確認があります。また、公開デモ用資格情報と管理者専用書籍の扱いについては、[PR #75](https://github.com/hiroyuki9614/book_library/pull/75) のセキュリティ確認を終えるまで未承認の公開変更を行いません。

実装済み範囲と未完了項目の正確な区別は [docs/current-status.md](docs/current-status.md) に記録しています。

## 開発者向け：ローカルで動かす

Node.js / npm と Docker が必要です。設定値は `.env.example` を元にローカルで用意し、**認証情報をGitへコミットしないでください**。

```bash
npm install
npm install --prefix backend
npm install --prefix frontend
npm run dev
```

- Frontend: `http://localhost:5173`
- Backend: `http://localhost:3000`
- DBを含む新規環境の準備やマイグレーションは [開発状況](docs/current-status.md) と [DB構造](docs/database.md) を参照してください。

**テスト・ビルド**

```bash
npm test
npm run build:backend
npm run build:frontend
npm --prefix frontend run test:e2e
```

E2Eは専用DBとテスト用ストレージで実行してください。本番DB・R2を使わないこと、デモ書籍の公開範囲と非公開書籍の保護を検証することが前提です。詳細は [ポートフォリオデモ運用手順](docs/portfolio-demo.md) を参照してください。

## 設計・実装を詳しく見る

- [実装状況と未完了項目](docs/current-status.md)
- [要件定義](docs/requirements.md)
- [API定義](docs/api.yaml)
- [データベース設計](docs/database.md) / [Prisma schema](backend/prisma/schema.prisma)
- [デザイン方針](docs/design.md)
- [運用・デモの安全条件](docs/portfolio-demo.md)

---

**License:** ISC
