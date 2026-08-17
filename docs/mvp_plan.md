# BeLib MVP 計画

- Updated: 2026-08-18
- Target requirements: `docs/requirements.md` v2.0.0
- Current implementation status: `docs/current-status.md`

## 1. この文書の責務

この文書は「次に何をどの順で完成させるか」と「各段階の完了条件」を管理します。

- 正式な機能要件は `requirements.md`
- 現在の実装事実は `current-status.md`
- DB構造は `backend/prisma/schema.prisma`
- 日付付きの実行証跡はGit履歴またはPersonal VaultのBeLib records

同じ進捗ログをこの文書へ複製しません。

## 2. 現在地

2026-08-14時点の実装checkpointでは、PDFのMVPコア縦切りが成立しています。

```text
login
→ role-authorized book list
→ protected PDF
→ page move
→ reading-info save to PostgreSQL
→ reload
→ saved page restore
```

さらに、最小admin APIで書籍メタデータとPDFを登録できます。

ただし、これは正式MVP全体の完了ではありません。現在のストレージは保護ローカル方式で、R2、EPUB、公開範囲選択、completed自動遷移、管理UI接続などが残っています。

`book_files.file_hash` のmigration/schema driftは、Fedora上のtask-owned PostgreSQL 16.14によるfresh DB runtime検証で解消済みです。これはPhase CのDB再現性に関する完了項目ですが、README/demoのfresh環境再現やMVP E2Eの再検証が残るため、正式なPhase C完了とは扱いません。

## 3. フェーズ

### Phase A: PDF core vertical slice

Status: **VERIFIED**

完了済み:

- Better Auth session
- `/api/v1/me`
- role-based book list/detail authorization
- protected PDF response
- PDF Reader API integration
- reading-info GET/PATCH
- PostgreSQL save/restore
- unauthorized 401 / forbidden 403
- real browser E2E

このフェーズを壊す変更では必ず回帰testを行います。

### Phase B: Minimal admin registration

Status: **BACKEND COMPLETE / UI NOT CONNECTED**

Backend完了済み:

- metadata registration API
- PDF upload API
- admin role check
- PDF metadata/header/size validation
- file hash保存
- DB失敗時の新規local file cleanup

未完了:

- Admin画面から実APIへ接続
- category一覧を実データから選択する経路
- 公開範囲を明示選択するUI/API契約

### Phase C: Reproducible MVP environment

Status: **IN PROGRESS / FRESH DB MIGRATION VERIFIED**

完了条件:

- committed migrationsだけでfresh DBをcurrent schemaへ到達させられる（完了）
- `book_files.file_hash` migration/schema driftがない（fresh runtimeで完了）
- READMEの手順でfresh環境を起動できる（未確認）
- backend build/testとMVP E2Eがfresh環境でも再現する（backend build/testは完了、MVP E2Eは未確認）
- 実ファイルや環境固有値をGitへ含めない（継続確認）

Fresh DB検証の記録:

- `prisma migrate deploy` は4 migrationを正常適用し、2回目は pendingなし
- `prisma migrate status` はDatabase schema is up to date
- `book_files.file_hash` は `VARCHAR(64) NOT NULL`
- `book_files_file_hash_key` はunique index
- `20260816140000_add_book_file_hash` はfinished / not rolled back
- Prisma validate、backend build、backend testはPASS（63 passed / 11 skipped）
- 正しいmigrationは `20260816140000_add_book_file_hash` のみ。`20260817120000_add_book_file_hash` は作成しない

frontendの既存build/typeエラーはこのbackend migration検証とは分離した既知課題であり、本フェーズのfresh DB検証成功・失敗には含めません。README/demo等の残条件も、この検証結果だけでは完了扱いにしません。

### Phase D: Formal storage boundary

Status: **NOT STARTED**

正式要件に従って、保護ローカル保存をCloudflare R2へ切り替えます。

完了条件:

- EPUB/PDFをR2へ保存できる
- 認証・閲覧権限を確認してからファイル取得手段を発行する
- 正式要件の有効期限・再発行ルールを満たす
- R2保存後のDB失敗時に補償処理がある
- file metadataとstorage objectが矛盾しない
- current PDF E2Eの認可境界を維持する

### Phase E: Publication scope and reading state

Status: **NOT STARTED**

完了条件:

- 書籍登録時に公開範囲を必ず選択する
- `all users` / `admin only` を表現できる
- default公開範囲を置かない
- 管理者限定書籍を一般ユーザーが取得できない
- 初回閲覧で `reading`
- PDF最終ページで `completed`
- completed後に戻っても状態を維持する

現在のadmin APIが自動でuser role permissionを作る動作は、このフェーズで正式要件へ合わせます。

### Phase F: EPUB vertical slice

Status: **NOT STARTED**

完了条件:

- EPUB登録
- protected EPUB取得
- Reader表示
- 位置保存・復元
- PC Chrome正常系
- Android Chrome主要正常系
- PDFと同じ認可境界を通る

### Phase G: Formal MVP management features

Status: **NOT STARTED / PARTIAL UI EXISTS**

対象:

- title/author search
- category filter / category management
- user management
- book metadata edit
- duplicate detection
- logical delete / restore
- permanent delete and recovery
- file replacement
- backup / restore acceptance

詳細な受け入れ条件は `requirements.md` を参照します。

## 4. 次の実装順

現在は次の順を推奨します。

1. fresh環境README/demo再現（file-hash migration検証後の残作業）
2. Admin UI -> existing admin API接続
3. publication scope正式化
4. PDF `completed` 自動遷移
5. R2へのstorage cutover
6. EPUB vertical slice
7. search/category/user/delete/replace等の管理機能
8. backup/restore・PC/Android最終受け入れ

理由:

- すでに成立したPDF coreを基準線として維持できる
- DB再現性を先に直すことで後続test環境の信頼性が上がる
- UI接続と公開範囲をR2前に整理し、storage移行とpermission変更を同時に抱えない
- R2とEPUBを別の検証単位に分け、失敗原因を局所化する

## 5. Task slicing rule

1 task = 1 observable result を基本とします。

良い例:

- fresh DBへfileHash migrationを適用できる
- Admin UIからmetadata APIへ1冊登録できる
- admin-only bookが一般ユーザー一覧へ出ない
- PDF最終ページでcompletedになる
- R2上の1冊を認可後に開ける

避ける例:

- 管理機能を全部完成させる
- R2/EPUB/公開範囲/削除を1変更で入れる

## 6. Formal MVP completion

正式MVP完了は `requirements.md` のMVP受け入れ条件を満たした時点です。

「PDF core VERIFIED」「Phase A完了」「backend admin API完了」などの部分完了を、正式MVP完了と読み替えません。
