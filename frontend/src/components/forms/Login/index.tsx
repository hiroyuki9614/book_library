import { Button } from '@/components/ui/button';
import { FieldGroup } from '@/components/ui/field';
import RenderInput from '@/components/RenderInput';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import * as z from 'zod';

type LoginFormValues = z.infer<typeof formSchema>;

const formSchema = z.object({
	userInput: z.string().min(1, 'ユーザーIDを入力してください。'),
	passwordInput: z.string().min(1, 'パスワードを入力してください。'),
});

function Login({ onSubmit, isPending }: { onSubmit: (values: LoginFormValues) => void | Promise<void>; isPending: boolean }) {
	const form = useForm<LoginFormValues>({
		resolver: zodResolver(formSchema),
		defaultValues: {
			userInput: '',
			passwordInput: '',
		},
	});

	const formItems = [
		{
			label: 'ユーザーID',
			placeholder: '',
			type: 'text',
			name: 'userInput',
			autoComplete: 'username',
		},
		{
			label: 'パスワード',
			placeholder: '',
			type: 'password',
			name: 'passwordInput',
			autoComplete: 'current-password',
		},
	] as const;

	return (
		<form id='form-rhf-demo' onSubmit={form.handleSubmit(onSubmit)}>
			<FieldGroup>
				{formItems.map((item) => (
					<RenderInput key={item.name} form={form} formItem={item} />
				))}
				<Button type='submit' className='mt-4' disabled={isPending}>
					Submit
				</Button>
			</FieldGroup>
		</form>
	);
}

export default Login;
