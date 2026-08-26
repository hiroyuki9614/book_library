# 開発ガイドライン

- 最終更新: 2026-08-14
- プロジェクトルールの正本: `../AGENTS.md`

この文書は日常的に参照する命名規則の要約です。`AGENTS.md` と矛盾する場合は `AGENTS.md` を優先します。

## TypeScript / React

- 変数・関数: `camelCase`
- React component: `PascalCase`
- 型: `PascalCase`
- 定数: `UPPER_SNAKE_CASE` を基本とする

## ファイル名

役割と既存構成へ合わせます。

- React component: `PascalCase` を優先
- 通常module: 既存のcamelCaseまたは小文字命名を維持
- 規約統一だけを目的に既存fileを大量renameしない

旧文書の「全ファイルをPascalCase」と読める規則は、現在の `api-client.ts`、`readingInfo.ts`、`routes.ts` などの構成と一致しないため廃止します。

## Database

- Prisma model: PascalCase単数形
- DB table / column: snake_case
- DB構造は `backend/prisma/schema.prisma` を正本とする

## Tooling

各packageの `package.json` に存在するscriptを使用します。存在しないformat/typecheck scriptを推測で実行しません。

## Documentation

- 正式要件: `requirements.md`
- 現在状態: `current-status.md`
- 現在実装済みAPI: `api.yaml`
- DB説明: `database.md`
- 実行計画: `mvp_plan.md`

暫定実装を正式要件として書き換えず、要件・実装・現在状態を分離して管理します。
