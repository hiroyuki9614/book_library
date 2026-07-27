import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import * as z from 'zod';
import RenderInput from '@/components/RenderInput';
import { FieldGroup } from '@/components/ui/field';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';
import { createUser } from '@/api/admin-users';
import { useState } from 'react';

const schema = z.object({
  name: z.string().min(1, '表示名を入力してください'),
  email: z.string().email('有効なメールアドレスを入力してください'),
  password: z.string().min(8, 'パスワードは8文字以上です'),
  role: z.enum(['user', 'admin']),
});

type FormValues = z.infer<typeof schema>;

export default function CreateUserForm({ onSuccess }: { onSuccess?: () => void }) {
  const form = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { name: '', email: '', password: '', role: 'user' } });
  const [showConfirm, setShowConfirm] = useState(false);
  const [pendingValues, setPendingValues] = useState<FormValues | null>(null);

  const submitHandler = (values: FormValues) => {
    if (values.role === 'admin') {
      setPendingValues(values);
      setShowConfirm(true);
      return;
    }

    return executeCreate(values);
  };

  const executeCreate = async (values: FormValues) => {
    const res = await createUser(values);
    if (!res.ok) {
      toast.error(res.error ?? 'ユーザー作成に失敗しました');
      return;
    }

    toast.success('ユーザーを作成しました');
    onSuccess?.();
    form.reset();
  };

  const confirmCreate = async () => {
    if (!pendingValues) return;
    setShowConfirm(false);
    await executeCreate(pendingValues);
    setPendingValues(null);
  };

  const cancelConfirm = () => {
    setShowConfirm(false);
    setPendingValues(null);
  };

  const formItems = [
    { label: '表示名', placeholder: '', type: 'text', name: 'name', autoComplete: 'name' },
    { label: 'メールアドレス', placeholder: '', type: 'email', name: 'email', autoComplete: 'email' },
    { label: '初期パスワード', placeholder: '', type: 'password', name: 'password', autoComplete: 'new-password' },
  ] as const;

  return (
    <>
      <form onSubmit={form.handleSubmit(submitHandler)}>
        <FieldGroup>
          {formItems.map((it) => (
            <RenderInput key={it.name} form={form} formItem={it} />
          ))}

          <div className='mt-2'>
            <label htmlFor='role' className='block mb-1'>権限</label>
            <select id='role' {...form.register('role')} className='border rounded p-2'>
              <option value='user'>一般ユーザー</option>
              <option value='admin'>管理者</option>
            </select>
          </div>

          <Button type='submit' className='mt-4'>作成</Button>
        </FieldGroup>
      </form>

      {showConfirm && (
        <div role='dialog' aria-modal='true' className='fixed inset-0 flex items-center justify-center'>
          <div className='bg-white p-6 rounded shadow-lg w-[480px]'>
            <h3 className='text-lg font-semibold mb-4'>管理者権限の付与</h3>
            <p className='mb-4'>管理者権限を付与します。よろしいですか？</p>
            <div className='flex gap-2 justify-end'>
              <Button variant='secondary' onClick={cancelConfirm}>キャンセル</Button>
              <Button onClick={confirmCreate}>管理者として作成</Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
