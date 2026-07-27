import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import RenderInput from '@/components/RenderInput';
import { Button } from '@/components/ui/button';
import { FieldGroup } from '@/components/ui/field';
import {
	adminRegistrationSchema,
	type AdminRegistrationFormValues,
} from '@/features/adminRegistration/adminRegistrationSchema';

type AdminRegistrationFormProps = {
	isPending: boolean;
	onSubmit: (values: AdminRegistrationFormValues) => void | Promise<void>;
};

export default function AdminRegistrationForm({
	isPending,
	onSubmit,
}: AdminRegistrationFormProps) {
	const form = useForm<AdminRegistrationFormValues>({
		resolver: zodResolver(adminRegistrationSchema),
		defaultValues: {
			name: '',
			email: '',
			password: '',
			passwordConfirmation: '',
		},
	});

	const formItems = [
		{
			label: '表示名',
			placeholder: '',
			type: 'text',
			name: 'name',
			autoComplete: 'name',
		},
		{
			label: 'メールアドレス',
			placeholder: '',
			type: 'email',
			name: 'email',
			autoComplete: 'email',
		},
		{
			label: 'パスワード',
			placeholder: '',
			type: 'password',
			name: 'password',
			autoComplete: 'new-password',
		},
		{
			label: 'パスワード（確認）',
			placeholder: '',
			type: 'password',
			name: 'passwordConfirmation',
			autoComplete: 'new-password',
		},
	] as const;

	return (
		<form onSubmit={form.handleSubmit(onSubmit)}>
			<FieldGroup>
				{formItems.map((item) => (
					<RenderInput key={item.name} form={form} formItem={item} />
				))}
				<Button type='submit' className='mt-4 w-full' disabled={isPending}>
					{isPending ? '登録中…' : '管理者を登録'}
				</Button>
			</FieldGroup>
		</form>
	);
}
