import { useEffect, useState, type FormEvent } from 'react';
import { KeyRound, RotateCcw, UserPlus, UserRoundX } from 'lucide-react';
import { toast } from 'sonner';

import {
	createAdminUser,
	disableAdminUser,
	fetchAdminUsers,
	resetAdminUserPassword,
	restoreAdminUser,
	type AdminUser,
} from '@/api/admin';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

function AdminUsers() {
	const [users, setUsers] = useState<AdminUser[]>([]);
	const [loading, setLoading] = useState(true);
	const [loadError, setLoadError] = useState<string | null>(null);
	const [email, setEmail] = useState('');
	const [name, setName] = useState('');
	const [password, setPassword] = useState('');
	const [creating, setCreating] = useState(false);
	const [mutatingUserId, setMutatingUserId] = useState<number | null>(null);
	const [resettingUserId, setResettingUserId] = useState<number | null>(null);
	const [temporaryPassword, setTemporaryPassword] = useState('');

	useEffect(() => {
		let mounted = true;
		fetchAdminUsers()
			.then((loadedUsers) => {
				if (!mounted) return;
				setUsers(loadedUsers);
				setLoadError(null);
			})
			.catch(() => {
				if (!mounted) return;
				setLoadError('ユーザー一覧の取得に失敗しました。再読み込みしてください。');
			})
			.finally(() => {
				if (mounted) setLoading(false);
			});
		return () => {
			mounted = false;
		};
	}, []);

	const handleCreateUser = async (event: FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		if (!email.trim() || !name.trim() || password.length < 8) return;
		setCreating(true);
		try {
			const created = await createAdminUser({ email, name, password });
			setUsers((current) => [created, ...current.filter((user) => user.id !== created.id)]);
			setEmail('');
			setName('');
			setPassword('');
			toast.success('一般ユーザーを登録しました。');
		} catch {
			toast.error('ユーザー登録に失敗しました。メールアドレスの重複や入力内容を確認してください。');
		} finally {
			setCreating(false);
		}
	};

	const handleDisable = async (userId: number) => {
		setMutatingUserId(userId);
		try {
			const result = await disableAdminUser(userId);
			setUsers((current) => current.map((user) => (
				user.id === userId ? { ...user, deletedAt: result.deletedAt } : user
			)));
			toast.success('ユーザーを利用停止しました。既存セッションは有効期限まで維持されます。');
		} catch {
			toast.error('ユーザーの利用停止に失敗しました。');
		} finally {
			setMutatingUserId(null);
		}
	};

	const handleRestore = async (userId: number) => {
		setMutatingUserId(userId);
		try {
			await restoreAdminUser(userId);
			setUsers((current) => current.map((user) => (
				user.id === userId ? { ...user, deletedAt: null } : user
			)));
			toast.success('ユーザーの利用を再開しました。');
		} catch {
			toast.error('ユーザーの利用再開に失敗しました。');
		} finally {
			setMutatingUserId(null);
		}
	};

	const startPasswordReset = (userId: number) => {
		setResettingUserId(userId);
		setTemporaryPassword('');
	};

	const handlePasswordReset = async (userId: number) => {
		if (temporaryPassword.length < 8) return;
		setMutatingUserId(userId);
		try {
			await resetAdminUserPassword(userId, temporaryPassword);
			setResettingUserId(null);
			setTemporaryPassword('');
			toast.success('仮パスワードを設定しました。既存セッションは維持されます。');
		} catch {
			toast.error('仮パスワードの設定に失敗しました。');
		} finally {
			setMutatingUserId(null);
		}
	};

	const activeCount = users.filter((user) => user.deletedAt === null).length;
	const disabledCount = users.length - activeCount;

	return (
		<main className='mx-auto w-full max-w-6xl space-y-6 p-4 md:p-8'>
			<header>
				<p className='mb-1 text-sm font-medium text-muted-foreground'>ADMIN CONSOLE</p>
				<h1 className='text-3xl font-semibold tracking-tight'>ユーザー管理</h1>
				<p className='mt-2 text-sm text-muted-foreground'>一般ユーザーの登録、利用停止・再開、仮パスワード再設定を行います。</p>
			</header>

			<section className='grid gap-4 sm:grid-cols-3'>
				<Card><CardHeader><CardDescription>一般ユーザー</CardDescription><CardTitle>{users.length}</CardTitle></CardHeader></Card>
				<Card><CardHeader><CardDescription>利用中</CardDescription><CardTitle>{activeCount}</CardTitle></CardHeader></Card>
				<Card><CardHeader><CardDescription>利用停止</CardDescription><CardTitle>{disabledCount}</CardTitle></CardHeader></Card>
			</section>

			<Card>
				<CardHeader>
					<CardTitle>一般ユーザーを登録</CardTitle>
					<CardDescription>メールアドレスは再利用不可です。初期パスワードは8文字以上で設定してください。</CardDescription>
				</CardHeader>
				<CardContent>
					<form className='grid gap-3 md:grid-cols-[1fr_1fr_1fr_auto]' onSubmit={handleCreateUser}>
						<Input aria-label='メールアドレス' type='email' placeholder='reader@example.com' value={email} onChange={(event) => setEmail(event.target.value)} required />
						<Input aria-label='表示名' placeholder='表示名' value={name} onChange={(event) => setName(event.target.value)} maxLength={255} required />
						<Input aria-label='初期パスワード' type='password' placeholder='8文字以上' value={password} onChange={(event) => setPassword(event.target.value)} minLength={8} required />
						<Button type='submit' disabled={creating || !email.trim() || !name.trim() || password.length < 8}>
							<UserPlus />{creating ? '登録中…' : '登録'}
						</Button>
					</form>
				</CardContent>
			</Card>

			<Card>
				<CardHeader>
					<CardTitle>一般ユーザー一覧</CardTitle>
					<CardDescription>利用停止しても読書記録と既存セッションは保持されます。メールアドレスは管理画面から変更しません。</CardDescription>
				</CardHeader>
				<CardContent className='overflow-x-auto'>
					{loading && <p className='mb-4 text-sm text-muted-foreground'>ユーザーを読み込んでいます…</p>}
					{loadError && <p role='alert' className='mb-4 text-sm text-destructive'>{loadError}</p>}
					<Table className='min-w-[900px]'>
						<TableHeader>
							<TableRow>
								<TableHead>表示名</TableHead>
								<TableHead>メールアドレス</TableHead>
								<TableHead>状態</TableHead>
								<TableHead className='w-[360px]'>操作</TableHead>
							</TableRow>
						</TableHeader>
						<TableBody>
							{users.map((user) => {
								const disabled = user.deletedAt !== null;
								const resetting = resettingUserId === user.id;
								return (
									<TableRow key={user.id}>
										<TableCell className='font-medium'>{user.name}</TableCell>
										<TableCell>{user.email}</TableCell>
										<TableCell><Badge variant={disabled ? 'outline' : 'secondary'}>{disabled ? '利用停止' : '利用中'}</Badge></TableCell>
										<TableCell>
											{resetting ? (
												<div className='flex gap-2'>
													<Input
														aria-label={`${user.name} の仮パスワード`}
														type='password'
														placeholder='8文字以上'
														value={temporaryPassword}
														onChange={(event) => setTemporaryPassword(event.target.value)}
													/>
													<Button size='sm' disabled={temporaryPassword.length < 8 || mutatingUserId === user.id} onClick={() => handlePasswordReset(user.id)}>設定</Button>
													<Button size='sm' variant='outline' onClick={() => { setResettingUserId(null); setTemporaryPassword(''); }}>キャンセル</Button>
												</div>
											) : (
												<div className='flex gap-2'>
													<Button size='sm' variant='outline' onClick={() => startPasswordReset(user.id)}><KeyRound />仮パスワード</Button>
													{disabled ? (
														<Button size='sm' variant='outline' disabled={mutatingUserId === user.id} onClick={() => handleRestore(user.id)}><RotateCcw />利用再開</Button>
													) : (
														<Button size='sm' variant='outline' disabled={mutatingUserId === user.id} onClick={() => handleDisable(user.id)}><UserRoundX />利用停止</Button>
													)}
												</div>
											)}
										</TableCell>
									</TableRow>
								);
							})}
							{!loading && users.length === 0 && (
								<TableRow><TableCell colSpan={4} className='py-8 text-center text-muted-foreground'>一般ユーザーはまだ登録されていません。</TableCell></TableRow>
							)}
						</TableBody>
					</Table>
				</CardContent>
			</Card>
		</main>
	);
}

export default AdminUsers;
