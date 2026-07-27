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
		<main className='flex h-screen items-center justify-center p-4'>
			<SectionCard pageTitle='Login Page' pageDescription='Please enter your credentials to log in.' size='large' className='w-[50%] text-center'>
				<LoginForm onSubmit={handleLogin} isPending={isPending || isSubmitting} />
				<p>current role: {role ?? '未ログイン'}</p>
			</SectionCard>
		</main>
	);
}
