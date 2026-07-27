import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import AdminRegistrationForm from '@/components/forms/AdminRegistration';
import SectionCard from '@/components/SectionCard';
import type { AdminRegistrationFormValues } from '@/features/adminRegistration/adminRegistrationSchema';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:3000';

type ApiError = {
	message?: string;
};

export default function AdminRegistration() {
	const navigate = useNavigate();
	const [isPending, setIsPending] = useState(false);

	const handleSubmit = async (values: AdminRegistrationFormValues) => {
		setIsPending(true);

		try {
			const response = await fetch(`${API_BASE_URL}/api/setup/admin`, {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					name: values.name.trim(),
					email: values.email.trim().toLowerCase(),
					password: values.password,
				}),
			});

			if (!response.ok) {
				const error = (await response.json().catch(() => ({}))) as ApiError;
				throw new Error(error.message ?? '管理者を登録できませんでした。');
			}

			toast.success('管理者を登録しました。ログインしてください。');
			navigate('/login');
		} catch (error) {
			toast.error(
				error instanceof Error ? error.message : '管理者を登録できませんでした。',
			);
		} finally {
			setIsPending(false);
		}
	};

	return (
		<main className='flex min-h-screen items-center justify-center p-4'>
			<SectionCard
				pageTitle='管理者登録'
				pageDescription='初回セットアップ用の管理者アカウントを登録します。'
				size='large'
				className='w-full'
			>
				<AdminRegistrationForm
					isPending={isPending}
					onSubmit={handleSubmit}
				/>
				<p className='mt-4 text-center text-sm'>
					既に登録済みの場合は{' '}
					<Link className='underline' to='/login'>
						ログイン
					</Link>
				</p>
			</SectionCard>
		</main>
	);
}
