import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import LoginForm from '@/components/forms/Login';
import SectionCard from '@/components/SectionCard';
import { useAuth } from '@/contexts/useAuth';
import { authClient } from '@/lib/auth-client';

export default function Login() {
	const { isPending, refreshAuth, role } = useAuth();
	const navigate = useNavigate();
	const [isSubmitting, setIsSubmitting] = useState(false);

	const handleLogin = async (values: { userInput: string; passwordInput: string }) => {
		setIsSubmitting(true);

		try {
			const result = await authClient.signIn.email({
				email: values.userInput,
				password: values.passwordInput,
			});

			if (result.error) {
				toast.error(result.error.message || '認証に失敗しました');
				return;
			}

			const currentUser = await refreshAuth();
			if (!currentUser) {
				toast.error('ログイン状態を確認できませんでした。');
				return;
			}

			toast.success('ログインしました。');
			navigate('/');
		} catch {
			toast.error('通信に失敗しました。時間を置いて再試行してください。');
		} finally {
			setIsSubmitting(false);
		}
	};

	return (
		<main className='flex min-h-screen items-center justify-center p-4'>
			<SectionCard pageTitle='BeLib デモ' pageDescription='EPUB / PDFを安全に読む個人ライブラリ' size='large' className='w-full max-w-2xl text-center'>
				<div className='mb-6 rounded-lg border bg-muted/40 p-4 text-left text-sm'>
					<p className='font-semibold'>採用面接向けデモ</p>
					<p className='mt-1 text-muted-foreground'>共有されたデモ用メールアドレスとパスワードでログインしてください。一般ユーザーには著作権保護期間が満了した公開デモ作品だけが表示されます。</p>
					<p className='mt-2 text-muted-foreground'>共有アカウントでは読書位置も共有されるため、前の利用者が保存した位置から始まる場合があります。</p>
				</div>
				<LoginForm onSubmit={handleLogin} isPending={isPending || isSubmitting} />
				<p className='mt-4 text-xs text-muted-foreground'>現在の権限: {role ?? '未ログイン'}</p>
			</SectionCard>
		</main>
	);
}
