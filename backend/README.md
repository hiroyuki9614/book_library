```
npm install
npm run dev
```

```
open http://localhost:3000
```

## 初期管理者の作成

管理者アカウントは、次の環境変数を実行時に設定してCLIから作成します。CLIは引数を受け付けません。
```bash
cd backend

# 推奨: 環境変数をファイルへ書いて一括で読み込む（.env.initial-admin を作成して使う）
# .env.initial-admin を Git にコミットしないでください。実行後は安全に削除または秘密管理へ移してください。
set -a
. ./.env.initial-admin
set +a

npm run create:initial-admin
```

代替: 実行環境の Secret Manager（クラウドの Secret Manager、OS のキーリング等）から環境変数を供給して `npm run create:initial-admin` を実行してください。コマンドラインでパスワードや完全な `DATABASE_URL` を直接展開して実行することは推奨しません。

同じメールアドレスの管理者が既に存在する場合、再実行しても User や Account を重複作成せず、名前とパスワードも変更しません。一般ユーザーが同じメールアドレスを使用している場合は、管理者への昇格を拒否します。
