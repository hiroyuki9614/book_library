type LoginForm = {
	label: string;
	placeholder: string;
	type: 'text' | 'password';
	name: 'userInput' | 'passwordInput';
	autoComplete: 'user-id' | 'current-password';
};

export const formItems: LoginForm[] = [
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
