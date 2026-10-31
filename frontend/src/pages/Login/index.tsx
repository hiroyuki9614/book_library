import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import LoginForm from '@/components/forms/Login';
import SectionCard from '@/components/SectionCard';
import { useAuth } from '@/contexts/useAuth';
import { authClient } from '@/lib/auth-client';
import { getPublicDemoCredentials } from '@/lib/public-demo-credentials';

export default function Login() {
	const { isPending, refreshAuth } = useAuth();
	const demoCredentials = getPublicDemoCredentials();
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
			<SectionCard pageTitle='BeLib' pageDescription='EPUB / PDFを安全に読む個人ライブラリ' size='large' className='w-full max-w-2xl text-center'>
				<div className='mb-6 rounded-lg border bg-muted/40 p-4 text-left text-sm'>
					<p className='font-semibold'>採用面接向けデモ</p>
					<p className='mt-1 text-muted-foreground'>デモ用の一般ユーザーとしてログインすると、著作権保護期間が満了した公開デモ作品だけを閲覧できます。</p>
					{demoCredentials ? (
						<div className='mt-3 rounded-md border bg-background p-3' aria-label='デモ用ログイン情報'>
							<p className='font-semibold'>デモ用アカウント</p>
							<dl className='mt-2 space-y-2'>
								<div>
									<dt className='text-muted-foreground'>ログインID（メールアドレス）</dt>
									<dd className='break-all font-mono select-all'>{demoCredentials.email}</dd>
								</div>
								<div>
									<dt className='text-muted-foreground'>パスワード</dt>
									<dd className='break-all font-mono select-all'>{demoCredentials.password}</dd>
								</div>
							</dl>
						</div>
					) : (
						<p className='mt-3 text-muted-foreground'>デモアカウントは現在公開準備中です。</p>
					)}
					<p className='mt-2 text-muted-foreground'>共有アカウントでは読書位置も共有されるため、前の利用者が保存した位置から始まる場合があります。</p>
				</div>
				<LoginForm onSubmit={handleLogin} isPending={isPending || isSubmitting} />
			</SectionCard>
		</main>
	);
}
