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
DATABASE_URL="<set-at-runtime>" \
INITIAL_ADMIN_EMAIL="admin@example.com" \
INITIAL_ADMIN_NAME="Admin User" \
INITIAL_ADMIN_PASSWORD="<set-at-runtime>" \
npm run create:initial-admin
```

同じメールアドレスの管理者が既に存在する場合、再実行してもUserやAccountを重複作成せず、名前とパスワードも変更しません。一般ユーザーが同じメールアドレスを使用している場合は、管理者への昇格を拒否します。
