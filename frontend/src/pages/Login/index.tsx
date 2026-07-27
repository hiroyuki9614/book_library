/* eslint-disable @typescript-eslint/no-explicit-any */
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/useAuth';
import LoginForm from '@/components/forms/Login';
import { toast } from 'sonner';
import SectionCard from '@/components/SectionCard';
import { authClient } from '@/lib/auth-client';

export default function Login() {
	const { role, isPending } = useAuth();
	const navigate = useNavigate();

	const handleLogin = async (values: { userInput: string; passwordInput: string }) => {
		try {
			// 二重送信防止は LoginForm の isPending を使っている想定
			const email = values.userInput;
			const password = values.passwordInput;

			// better-auth のクライアント実装は型が変わる可能性があるため一箇所だけ any を許容
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			const res = await (authClient as any).signIn.email({ email, password });
			// better-auth のクライアント実装はバージョン差があるため any を使いつつ
			// 成功時はトップへ遷移し、AuthProvider がセッション更新を検知する想定
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			if (!res || (res as any).error) {
				toast.error(`ログイン失敗: ${(res as any)?.error ?? '認証エラー'}`);
				return;
			}

			toast.success('Logged in successfully!');
			navigate('/');
		} catch (error) {
			toast.error(`ログイン失敗: ${error instanceof Error ? error.message : '不明なエラー'}`);
		}
	};

	return (
		<main className='flex h-screen items-center justify-center p-4'>
			<SectionCard pageTitle='Login Page' pageDescription='Please enter your credentials to log in.' size='large' className='w-[50%] text-center'>
				<LoginForm onSubmit={handleLogin} isPending={isPending} />

				<p className='mt-4 text-sm text-neutral-500'>ログイン時には、フロント側でパスワードの8文字制限してください。既存のユーザーはDBリセットするため。</p>
				<p>current role: {role ?? '未ログイン'}</p>
			</SectionCard>
		</main>
	);
}
