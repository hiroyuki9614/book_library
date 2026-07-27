import SectionCard from '@/components/SectionCard';
import CreateUserForm from '@/components/forms/CreateUser';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/useAuth';

export default function NewUserPage() {
  const { role } = useAuth();
  const navigate = useNavigate();

  if (role !== 'admin') {
    navigate('/');
    return null;
  }

  return (
    <main className='p-6'>
      <SectionCard pageTitle='ユーザー作成' pageDescription='管理者が新しいユーザーを作成します' size='large'>
        <CreateUserForm onSuccess={() => navigate('/admin/users')} />
      </SectionCard>
    </main>
  );
}
