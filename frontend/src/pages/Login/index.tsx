import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/useAuth';
import LoginForm from '@/components/forms/Login';
import { toast } from 'sonner';
import SectionCard from '@/components/SectionCard';
import { ReactReader } from 'react-reader';

export default function Login() {
	const { login, role } = useAuth();
	const navigate = useNavigate();

	const handleLogin = () => {
		toast.success('Logged in successfully!');
		login('user');
		navigate('/');
	};

	return (
		<main className='flex items-center justify-center p-4 h-screen'>
			<SectionCard pageTitle='Login Page' pageDescription='Please enter your credentials to log in.' size='large' className='text-center w-[50%]'>
				<LoginForm onSubmit={handleLogin} />
				<p>current role: {role ?? '未ログイン'}</p>
			</SectionCard>
		</main>
	);
}
