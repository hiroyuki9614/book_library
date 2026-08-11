# BeLib Current Implementation Status

- Updated: 2026-08-11
- Purpose: MVPの「実装済み」「仮実装」「未接続」「契約未整理」を短時間で把握するための現状記録
- GitHub source snapshot checked before this documentation commit: `fc51ac1d31a52202516c5bfa713d66be50b8e40c`
- Separate local evaluation snapshot: `ef5bedc1182c2ae58bc724fe594453e9ef8dbe62`

## 1. Snapshot identity note

2026-08-11のAI Agent Orchestration A/B評価は、ローカル `bookLibrary` の次のHEADで実行された。

```text
ef5bedc1182c2ae58bc724fe594453e9ef8dbe62
```

このcommitは、この文書作成時点ではGitHub `hiroyuki9614/book_library` の検索可能なcommit history上で確認できなかった。

一方、GitHub `main` のsource codeを別途再確認し、少なくとも本書に記載する主要な実装状態が現在のGitHub sourceでも成立していることを確認した。

ローカル評価HEADとGitHub mainを同一identityとして扱わないこと。

## 2. Current high-level state

BeLibはMVP実装途中である。

現在は:

```text
認証基盤                 実装済み / API接続あり
Prisma主要モデル         実装済み
書籍一覧UI               実装済みだがmock依存
書籍一覧API              route skeletonあり / main app未接続
書籍詳細API              未実装
読書進捗UI               一部実装 / mock・local保存依存
読書進捗API              未実装
管理画面書籍登録         UIあり / local state仮実装
DB書籍登録               未実装
R2ファイル登録           未実装
RoleBookPermission本結合 未実装
```

フロントエンドUIとDB schemaの土台はかなり存在するが、MVPの主要利用経路を成立させるBackend APIとFrontend本結合がまだ残っている。

## 3. Backend

### 3.1 Connected routes

`backend/src/index.ts` で現在確認できる主なroute:

```text
GET  /health
GET/POST /api/auth/*
GET  /api/v1/me
GET  /doc
GET  /scalar
POST /test
```

`/api/v1/me` とbetter-authの認証基盤はmain appへ接続されている。

### 3.2 Books route

`backend/src/routes/books/routes.ts` は存在する。

現在の実装は概ね:

```ts
const prisma = c.get('prisma');
const books = await prisma.book.findMany();
return c.json(books);
```

であり、次は未実装または未接続:

- `backend/src/index.ts` へのbooks router mount
- `withPrisma` または同等のPrisma注入
- 認証
- `role_book_permissions` による閲覧権限
- `deletedAt` 除外
- pagination
- title / author search
- category filter
- read status filter
- API DTOへのresponse mapping
- `ReadingInfo` / `BookFile` からのderived fields構築

したがって、現状の `GET /api/v1/books` は本番APIとして成立していない。

### 3.3 Book detail

API仕様には書籍詳細取得が存在するが、current sourceには対応する `GET /:bookId` 実装を確認できない。

実装時は少なくとも:

- bookId validation
- authentication
- role-based permission
- logical delete handling
- Book / Category / BookFile / current user's ReadingInfo取得
- API response mapping

の責務整理が必要。

### 3.4 Reading information

Prismaには `ReadingInfo` modelが存在する。

```text
userId + bookId = unique
currentPosition = nullable
readStatus default = unread
```

一方、API仕様にある:

```text
GET   /books/{bookId}/reading-info
PATCH /books/{bookId}/reading-info
```

に対応するruntime routeは未実装。

### 3.5 CORS

現在の`/api/*` CORS許可methodは:

```text
GET
POST
OPTIONS
```

で、reading-info API仕様が使用する `PATCH` は含まれていない。

読書進捗APIを本結合する際に整合させる必要がある。

## 4. Database / Prisma

`backend/prisma/schema.prisma` にはMVP主要モデルが既に存在する。

```text
User
Session
Account
Verification
Role
Book
Category
BookFile
ReadingInfo
RoleBookPermission
```

書籍・読書進捗APIを実装するだけで新しいドメインtableを追加する前提にはしない。

### Book

主な現行field:

```text
title
authorName
publishedAt
publisher
description
deletedAt
categoryId
pageTurnDirection
```

### BookFile

主な現行field:

```text
extension
mimeType
fileUrl
originalFileName
storedFileName
fileSize
fileHash
bookId
```

`fileHash` はunique。

### ReadingInfo

```text
currentPosition
readStatus
userId
bookId
```

`userId + bookId` はcompound unique。

### RoleBookPermission

```text
roleId
bookId
```

`roleId + bookId` はcompound unique。

MVPの書籍閲覧権限はこのmodelを利用し、`books.role_id`等の別permission構造を追加しない。

## 5. Frontend

### 5.1 Book list

`frontend/src/hooks/useBooks.ts` は現在:

```text
booksApiMock()
```

をReact Queryから呼んでいる。

したがって書籍一覧画面はUIとして存在するが、本番books APIへ未接続。

本結合時は、現在のFrontend側検索・filter・pagination責務とBackend API側のpagination/filter責務が二重にならないよう整理する。

### 5.2 Reading progress

`frontend/src/hooks/useReadingProgresses.ts` も現在mock依存。

API仕様の個別book reading-infoと、現在のFrontend data shapeを統一する必要がある。

AI Agent Orchestration評価では、Frontendのprogress型とAPI契約に以下の差が確認された。

```text
Frontend側:
id
status
currentPage
progress

API側:
bookId
readStatus
currentPosition
progress
```

この差をそのままDB schemaへ反映するのではなく、API DTO / frontend model / DB modelの責務を決める。

### 5.3 Admin book registration

`frontend/src/pages/Admin/index.tsx` は現在、初期mock dataを `useState` で保持している。

登録時も:

```text
setBooks(...)
```

で画面内stateへ追加するのみ。

そのため:

- DBへ保存しない
- reloadで消える
- R2へ保存しない
- Backend admin APIを呼ばない
- RoleBookPermissionを作らない

現状はUI prototypeとして扱う。

## 6. Requirements that are already defined

`docs/requirements.md` には、MVPの主要挙動がかなり具体的に定義されている。

### Book registration

管理者書籍登録の主要経路:

1. EPUB/PDF選択
2. extension / MIME / content validation
3. file hash重複確認
4. metadata取得
5. 管理者による修正
6. category選択
7. 公開範囲選択
8. R2保存
9. DB保存
10. 全成功時だけ完了

重要な確定要件:

- EPUB/PDF
- 最大200MB
- file hashによる重複禁止
- MVPでは1冊1file
- R2とDBの片方だけを残さない
- category必須。未選択時は「未分類」
- 公開範囲必須
- 公開範囲は「全ユーザー公開」または「管理者のみ」

### Reading state

確定要件:

```text
unread
reading
completed
```

- 初回閲覧でreading
- 最終ページ表示でcompleted
- completed後に前へ戻ってもcompleted維持
- ユーザーによる手動status変更は禁止
- 管理者も自身の記録だけを通常ユーザー同様に保持
- 管理者は他ユーザーの進捗を閲覧しない

### Progress

- PDF: current page / total pagesから算出
- EPUB: positionから算出可能な場合のみ算出
- progress率をDBへ別columnとして保存しない

## 7. Contract inconsistencies to resolve

### 7.1 `finished` vs `completed`

API仕様のBook schemaには:

```text
unread
reading
finished
```

が残っている。

一方、`AGENTS.md`、Prisma関連運用、既存テスト、requirementsでは:

```text
unread
reading
completed
```

を正規値として扱っている。

現時点では新しいstatusを増やさず、実装前にAPI仕様を正本へ同期する。

### 7.2 `author` vs `authorName`

API schema:

```text
author
```

Prisma:

```text
authorName
```

DTO mappingとして維持するのか、契約名を統一するのかを決める。

DB field名をAPIに合わせるためだけに無条件でmigrationしない。

### 7.3 `categoryId` nullable vs required

API schemaでは `categoryId` nullable。

Prismaでは `Book.categoryId` required。

requirementsでは未選択時に「未分類」を利用する。

DB schemaを正本としつつ、APIでnullを受けるのか、Frontendから必ずIDを送るのかを確定する。

### 7.4 `readStatus` update responsibility

requirementsではユーザーによる手動status変更は禁止。

一方、reading-info PATCHの契約や既存RED testではreadStatus更新を扱う箇所がある。

「Readerの自動状態遷移を同じPATCHで保存する」のか、「clientが任意statusを指定できる」のかを分離して契約化する必要がある。

### 7.5 `progress`

API / Frontendにはprogress表現が存在するが、Prisma ReadingInfoにはprogress columnがない。

これはrequirementsと整合しており、原則としてDB column追加ではなくcurrentPosition等から算出する責務を検討する。

## 8. Tests observed during 2026-08-11 evaluation

AI Agent Orchestration評価のローカルsnapshotでは、Backend contract test実行時に未実装APIが404となる状態が確認された。

代表的な観測:

```text
backend/src/api.mvp.red.test.ts
- book list/detail/reading-infoにRED contract testsあり
- 未実装routeは404
- 管理系testの一部はskip
```

Frontendではmockベースのhook / BookTable / Admin UI testが存在する。

このsectionは2026-08-11ローカル評価snapshotの観測記録であり、GitHub main上の最新test resultを永久保証するものではない。

次回実装時はcurrent HEADで再実行すること。

## 9. Recommended MVP implementation order

現状からの最短ルート候補:

```text
1. API / DB / Frontend contractの矛盾を必要最小限解消
2. GET /api/v1/books をmountして本実装
3. 書籍詳細取得を実装
4. reading-info GET/PATCHを実装
5. useBooks / useReadingProgresses / Readerを実APIへ本結合
6. admin book registration API + R2 + DB永続化
7. RoleBookPermissionを登録・閲覧経路へ本結合
8. mock/local-state依存をMVP経路から除去
```

ただし各機能は一度に大規模変更せず、既存RED testまたは小さなfeature test単位で進める。

## 10. Current definition of done for MVP core path

少なくとも以下が実データで通るまでは「UIがある」ことと「MVP利用経路が完成」を区別する。

```text
login
↓
roleで許可されたbook list取得
↓
book detail取得
↓
EPUB/PDFを認可付きで開く
↓
reading position/statusをserver保存
↓
再訪問で復元

admin login
↓
book metadata + file登録
↓
R2保存
↓
DB保存
↓
公開範囲permission保存
↓
一般ユーザー側一覧へ反映
```

## 11. Documentation maintenance rule

この文書は現在状態のsnapshotであり、requirementsやPrisma schemaを置き換えない。

優先順位:

```text
current user request
> AGENTS.md
> backend/prisma/schema.prisma for DB structure
> docs/requirements.md for behavior
> docs/api.yaml for API contract
> this current-status document
```

実装が進んだ場合は、完了した項目をこの文書でも更新し、mock / local-only / unmounted等の記述を放置しない。