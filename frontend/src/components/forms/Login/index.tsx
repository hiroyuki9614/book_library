import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from '@/components/ui/field';
import { zodResolver } from '@hookform/resolvers/zod';
import { Controller, useForm } from 'react-hook-form';
import * as z from 'zod';

const formSchema = z.object({
	title: z.string().min(0, 'Bug title must be at least 5 characters.').max(32, 'Bug title must be at most 32 characters.'),
	description: z.string().max(100, 'Description must be at most 100 characters.'),
});

function Login({ onSubmit }: { onSubmit: () => void }) {
	const form = useForm({
		resolver: zodResolver(formSchema),
		defaultValues: {
			title: '',
			description: '',
		},
	});

	const formItems = [
		{
			label: 'ユーザーID',
			placeholder: '',
			type: 'text',
			name: 'userInput',
			autoComplete: 'user-id',
		},
		{
			label: 'パスワード',
			placeholder: '',
			type: 'password',
			name: 'passwordInput',
			autoComplete: 'current-password',
		},
	];

	return (
		<form id='form-rhf-demo' onSubmit={form.handleSubmit(onSubmit)}>
			<FieldGroup>
				<Controller
					name='description'
					control={form.control}
					render={({ field, fieldState }) => (
						<Field data-invalid={fieldState.invalid}>
							{formItems.map((item) => (
								<div key={item.name} id={item.name}>
									<FieldLabel htmlFor={item.name}>{item.label}</FieldLabel>
									<Input {...field} type={item.type} id={item.name} className='resize-none' aria-invalid={fieldState.invalid} autoComplete={item.autoComplete} />
									<p>{fieldState.invalid && <FieldError errors={[fieldState.error]} />}</p>
								</div>
							))}
						</Field>
					)}
				/>
				<Button type='submit' className='mt-4'>
					Submit
				</Button>
			</FieldGroup>
		</form>
	);
}

export default Login;
