# 画面定義書

- 最終更新: 2026-08-14
- 実routeの正本: `frontend/src/routes/AppRoutes.tsx`

この文書は画面の役割と共通デザイン方針を説明します。route自体はfrontend実装を優先します。

## 共通デザイン

- 背景色: `#EEECDC`
- 文字色: `#282828`
- サイドナビ: `#1F293D`
- サイドナビ文字: `#FDFDFD`
- modal背景: `#FDFDFD`
- overlay: `#D9D9D9`
- UI基盤: shadcn/ui + Tailwind CSS

既存componentやdesign tokenが実装済みの場合、デザイン変更依頼なしに全面変更しません。

## 現在の画面

| route | guard | 役割 |
| --- | --- | --- |
| `/login` | guest only | ログイン |
| `/` | authenticated | Home。書籍一覧の入口 |
| `/reader/:id` | authenticated | EPUB/PDF Reader。現在の実API縦切りはPDF中心 |
| `/admin` | authenticated + admin | 書籍管理UI。現在は登録処理の一部がlocal state |
| `/about` | authenticated | About |
| `/contact` | authenticated | Contact |
| `*` | authenticated layout内 | 404 |

旧文書にあった「ダッシュボード」「書籍一覧画面」「マイページ」という独立画面前提は、現在のrouterと一致しないため削除します。

## 主なUI部品

- side navigation
- button
- card
- modal / sheet
- form
- table
- dropdown menu
- loading / skeleton
- Reader controls

## Figma

非公開Figmaは視覚設計の補助資料です。現在routeや実装状態の正本としては扱いません。
