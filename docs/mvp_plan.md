# BeLib MVP 計画

- Updated: 2026-08-26
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

現在のmainでは、PDF/EPUB閲覧、読書情報、管理API、管理UIの主要なnon-R2経路が実装されています。

```text
login
→ role-authorized book list
→ protected PDF/EPUB
→ page/location move
→ reading-info save to PostgreSQL
→ reload
→ saved position restore
```

さらに、Admin UIから公開範囲を指定してEPUB/PDFとメタデータを登録し、保存済み書籍、カテゴリ、一般ユーザーを管理できます。

ただし、これは正式MVP全体の完了ではありません。現在のストレージは保護ローカル方式で、R2、signed URL、完全削除、ファイル差し替え、進捗率の最終仕様、backup/restore、PC/Android最終受入が残っています。

`book_files.file_hash` のmigration/schema driftは、committed migrationと現在schemaの検証対象として解消済みです。fresh環境での実行結果はコマンド実行時の検証記録として扱い、未実行の受入を完了扱いにしません。

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

Status: **VERIFIED FOR CURRENT NON-R2 PATH**

完了済み:

- Admin UIからfull registration APIへ接続
- active category一覧の取得と選択
- EPUB/PDFの拡張子、MIME、内容、200MB上限の検証
- 明示的な`all_users` / `admin_only`選択とRoleBookPermission保存
- SHA-256 file hashによる重複拒否
- DB失敗時のprotected local file cleanup
- reload後の保存済みAdmin book list表示

formal storageとして残るものはPhase Dで扱います。

### Phase C: Reproducible MVP environment

Status: **VERIFIED**

実装・migration確認済み:

- committed migrationsだけでfresh DBをcurrent schemaへ到達させられる（完了）
- `book_files.file_hash` migration/schema driftがない（fresh runtimeで完了）
- Prisma validate、backend build、backend testはPASS（63 passed / 11 skipped）
- READMEのfresh setup手順と、実ファイル・環境固有値をGitへ含めない運用は文書化済み
- fresh DBでのmigration適用、backend build/test、MVP E2Eの再実行は環境依存の検証として、各コマンドの実行結果を別途確認する
- frontend全体build/testには既知課題があり、focused CIの成功と正式MVP受入は分離して判定する

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

Status: **PARTIAL: NON-R2 IMPLEMENTATION VERIFIED**

実装済み:

- 公開範囲をAdmin UI/APIで明示選択
- `all users` / `admin only` を表現できる
- default公開範囲を置かない
- 管理者限定書籍の一般ユーザー閲覧拒否
- 初回閲覧で`reading`
- PDF最終ページ/EPUB最終locationで`completed`
- completed後の途中位置保存でもcompletedを維持

残るformal条件:

- R2上の公開とsigned URL semantics
- 非公開化・論理削除中の既存画面を含む正式なURL lifecycle受入

### Phase F: EPUB vertical slice

Status: **PARTIAL: IMPLEMENTED, FINAL DEVICE ACCEPTANCE OPEN**

完了条件:

- EPUB登録
- protected EPUB取得
- Reader表示
- 位置保存・復元
- PC Chrome正常系の最終受入
- Android Chrome主要正常系の最終受入
- PDFと同じ認可境界を通る

### Phase G: Formal MVP management features

Status: **PARTIAL: CORE MANAGEMENT FLOWS IMPLEMENTED**

対象:

- title/author search（backendとHomeの一覧内filter）
- category management（backend/Admin UI）
- user management（backend/Admin Users UI）
- book metadata edit / publication scope edit（backend/Admin UI）
- duplicate detection
- logical delete / restore

未完了:

- Homeのcategory filter UI
- permanent delete and recovery
- file replacement
- backup / restore acceptance
- reader-derived progress percentage

詳細な受け入れ条件は `requirements.md` を参照します。

## 4. 次の実装順

現在は次の順を推奨します。

1. Cloudflare R2 storage adapterとsigned URL lifecycle
2. R2を前提とした完全削除・ファイル差し替え・復旧
3. Homeのcategory filter UIとreader-derived progress percentage
4. PostgreSQL backup/restore acceptance
5. PC Chrome / Android Chromeの最終受入

理由:

- すでに成立したPDF coreを基準線として維持できる
- DB再現性を先に直すことで後続test環境の信頼性が上がる
- non-R2の管理・公開範囲・読書状態を基準線として、storage移行の影響を分離できる
- R2 object lifecycleと既存の論理削除/読書記録保持を同時に受入できる
- UIの未接続部分と正式なbackup/device受入を残タスクとして明示できる

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
