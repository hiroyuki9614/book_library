import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/useAuth';
import LoginForm from '@/components/forms/Login';
import { toast } from 'sonner';
import SectionCard from '@/components/SectionCard';
import useUser from '@/hooks/useUser';

export default function Login() {
	const { login, role } = useAuth();
	const navigate = useNavigate();
	const { mutateAsync, isPending } = useUser();

	const handleLogin = async (values: { userInput: string; passwordInput: string }) => {
		try {
			const user = await mutateAsync(Number(values.userInput));
			console.log('Fetched user:', user);
			toast.success('Logged in successfully!');
			login(user.role);
			navigate('/');
		} catch (error) {
			toast.error(`ログイン失敗: ${error instanceof Error ? error.message : '不明なエラー'}`);
		}
	};

	return (
		<main className='flex h-screen items-center justify-center p-4'>
			<SectionCard pageTitle='Login Page' pageDescription='Please enter your credentials to log in.' size='large' className='w-[50%] text-center'>
				<LoginForm onSubmit={handleLogin} isPending={isPending} />

				<p>current role: {role ?? '未ログイン'}</p>
			</SectionCard>
		</main>
	);
}
