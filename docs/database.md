# データベース定義書

- 最終更新: 2026-11-12
- DB構造の正本: `backend/prisma/schema.prisma`
- ER図: `docs/ER図.svg`

この文書はPrisma schemaの説明用です。型、nullable、default、index、unique、relationは `schema.prisma` を正とします。過去のSpreadsheetは補助資料であり、現行DB構造の正本ではありません。

## 現在のモデル

- User
- Session
- Account
- Verification
- Role
- Book
- Category
- BookFile
- ReadingInfo
- RoleBookPermission

## 主な制約

- `Book.categoryId` はrequired
- `Book.pageTurnDirection` のdefaultは `ltr`
- `BookFile.fileHash` はunique
- `ReadingInfo.currentPosition` はnullable PostgreSQL `TEXT`。APIは非空文字列を最大16,384文字まで受け付ける
- `ReadingInfo.progressPercentage` はreaderが有効な位置と総量を計測できた場合のnullable整数（0〜100）
- `ReadingInfo.progressPercentage` はIssue #90の§6.8例外として、セッションをまたぐ本棚・ホーム表示用の派生スナップショットだけを保持する。総ページ数やEPUB location総数、読書履歴は追加しない
- `ReadingInfo.readStatus` のdefaultは `unread`
- `ReadingInfo.saveSequence` は per-user/book のCAS版で、PostgreSQL `BIGINT`・default `0`。APIではJSON numberの丸めを避けるため10進文字列として扱い、履歴や端末間の実時間順序は保持しない
- `ReadingInfo` は `userId + bookId` でunique
- `RoleBookPermission` は `roleId + bookId` でunique

読書状態は `unread` / `reading` / `completed` を使用します。

`BookFile.fileUrl` は公開URL専用ではなく、現在のPDF MVPでは保護storageの相対キーとして使われています。正式なstorage要件は `requirements.md`、現在状態は `current-status.md` を参照してください。

## リレーション概要

```text
Role 1 --- N User
User 1 --- N Session
User 1 --- N Account
Category 1 --- N Book
Book 1 --- N BookFile
User 1 --- N ReadingInfo
Book 1 --- N ReadingInfo
Role 1 --- N RoleBookPermission
Book 1 --- N RoleBookPermission
```

## 更新時

schema変更時は、必要に応じてPrisma migration、generated client、ER図、関連API/type/test文書を同期します。

現在のmigration再現性に関する既知課題は `current-status.md` で管理します。

## ER図

![ER図](./ER図.svg)
