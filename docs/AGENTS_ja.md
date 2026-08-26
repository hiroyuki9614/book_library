# AGENTS.md 日本語補助

- 最終更新: 2026-08-14
- 運用ルールの正本: `../AGENTS.md`

この文書はroot `AGENTS.md` の日本語補助です。ルールを二重に独立管理しません。矛盾がある場合はroot `AGENTS.md` を優先します。

## プロジェクト方針

BeLibはEPUB/PDFをbrowserで管理・閲覧する個人向けWebアプリです。

MVPでは機能数より「小さく一連の操作が動くこと」を優先し、依頼されていないsecond-phase機能を先行追加しません。

## 現在の技術構成

Frontend:

- React
- TypeScript
- Vite
- React Router
- Tailwind CSS
- shadcn/ui

Backend:

- Node.js
- Hono
- Prisma
- PostgreSQL
- Better Auth

## 現在のモデル境界

認証基盤:

- User
- Session
- Account
- Verification
- Role

BeLib domain:

- Book
- Category
- BookFile
- ReadingInfo
- RoleBookPermission

DB構造の正本は `backend/prisma/schema.prisma` です。

## 文書の役割

- `requirements.md`: 正式に満たすべき要件
- `current-status.md`: 現在の実装状態と既知drift
- `api.yaml`: 現在実装済みの `/api/v1` contract
- `mvp_plan.md`: 次の実装順と完了gate
- `database.md`: schemaの説明
- `design.md`: UI/design補助
- `guidline.md`: 日常の命名・開発規約
- `ER図.svg`: schema由来のrelation図

暫定実装を理由に正式要件を黙って弱めません。現在の実装が要件へ未到達なら `current-status.md` へdriftとして残します。

`docs/old/` は履歴資料なので、現在仕様に見えるよう刷新しません。

## 実装ルール

- 変更は小さく保つ
- 関係ないcodeをrewriteしない
- public behaviorを依頼なしで変更しない
- 不要な抽象化やlibrary追加を避ける
- 認証方式を依頼なしで変更しない
- frontendだけで閲覧可否を決めない
- uploadされた書籍実ファイルをGitへ含めない
- mock/local stateと実APIを明確に区別する

## DBルール

- relationはforeign keyで表現する
- `ReadingInfo`: `userId + bookId` unique
- `RoleBookPermission`: `roleId + bookId` unique
- 書籍閲覧permissionは `role_book_permissions` を使う
- credentialを独自User columnへ重複追加しない
- committed済みmigrationを履歴合わせだけのために書き換えない
- schema変更はforward migrationとfresh DB再現性を確認する

読書状態:

- `unread`
- `reading`
- `completed`

ページ送り方向:

- `ltr`
- `rtl`

## 命名

- Prisma model: PascalCase単数形
- DB table / column: snake_case
- TypeScript変数・関数: camelCase
- React component / type: PascalCase
- 通常file名は既存の役割別慣習へ合わせ、全部をPascalCaseへ強制しない

## Test

機能追加・bug fixでは、意味のある自動testが書けるなら最低1つ追加または更新します。

文書だけの変更はbehaviorを変えない限り自動test不要です。

実際に `package.json` に存在するscriptだけを使い、存在しないcommandを推測しません。

## 完了報告

最終応答では次を示します。

1. 変更内容
2. 実行したtest/check
3. 未完了・follow-up
