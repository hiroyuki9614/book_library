type User = {
	id: number;
	name: string;
	role: 'admin' | 'user';
};

async function userApiMock(id: number, timeout: number): Promise<User[]> {
	const users: User[] = [
		{
			id: 1,
			name: 'ユーザー1',
			role: 'admin',
		},
		{
			id: 2,
			name: 'ユーザー2',
			role: 'user',
		},
	];

	try {
		const result = await new Promise<User[]>((resolve) => {
			setTimeout(() => {
				resolve(users);
			}, timeout);
		});
		const fetchedUser = result.find((user) => user.id === id);
		if (!fetchedUser) {
			throw new Error(`ユーザーが見つかりませんでした。ID: ${id}`);
		}

		return fetchedUser ? [fetchedUser] : [];
	} catch (error) {
		console.error('ユーザーの取得に失敗:', error);
		throw error;
	}
}

export default userApiMock;
