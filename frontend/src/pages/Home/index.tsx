import { BookTable } from '@/components/BookTable';
import { MiniCard } from '@/components/MiniCard';
import SectionCard from '@/components/SectionCard';
import useBooks from '@/hooks/useBooks';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useState } from 'react';

function Home() {
	const { books, isLoading, isError } = useBooks();
	const [selectedSort, setSelectedSort] = useState('newest');
	const [searchQuery, setSearchQuery] = useState('');
	type StatusFilter = 'all' | 'unread' | 'reading' | 'completed';
	const [selectedStatus, setSelectedStatus] = useState<StatusFilter>('all');

	const itemsPerPage = 5;
	const statusCounts = books.reduce(
		(acc, book) => {
			const status = book.status.toLowerCase() as Exclude<StatusFilter, 'all'>;
			acc[status] += 1;
			return acc;
		},
		{ unread: 0, reading: 0, completed: 0 },
	);
	const countLabel = (count: number) => {
		if (isError) return '取得失敗';
		if (isLoading) return '読み込み中';
		return `${count}冊`;
	};
	// The existing API provides saved statuses, not reader-derived percentages.
	const progressLabel = '未計測';

	return (
		<>
			<div className='m-4'>
				<div className='md:hidden'>
					<SectionCard size='full'>
						<div className='space-y-4'>
							<h2 className='font-semibold'>読書サマリー</h2>
							<div className='grid grid-cols-2 gap-3 text-sm'>
								<div><p className='text-muted-foreground'>総冊数</p><p className='font-semibold'>{countLabel(books.length)}</p></div>
								<div><p className='text-muted-foreground'>読書中</p><p className='font-semibold'>{countLabel(statusCounts.reading)}</p></div>
								<div><p className='text-muted-foreground'>読了</p><p className='font-semibold'>{countLabel(statusCounts.completed)}</p></div>
								<div><p className='text-muted-foreground'>未読</p><p className='font-semibold'>{countLabel(statusCounts.unread)}</p></div>
								<div><p className='text-muted-foreground'>平均進捗</p><p className='font-semibold'>{progressLabel}</p></div>
							</div>
						</div>
					</SectionCard>
				</div>

				<div className='hidden gap-4 md:flex'>
					<MiniCard title='総冊数' color='bg-primary/20'><p>{countLabel(books.length)}</p></MiniCard>
					<MiniCard title='読書中' color='bg-blue-500/20'><p>{countLabel(statusCounts.reading)}</p></MiniCard>
					<MiniCard title='読了' color='bg-red-500/20'><p>{countLabel(statusCounts.completed)}</p></MiniCard>
					<MiniCard title='未読' color='bg-green-500/20'><p>{countLabel(statusCounts.unread)}</p></MiniCard>
					<MiniCard title='平均進捗' color='bg-purple-500/20'><p>{progressLabel}</p></MiniCard>
				</div>
			</div>

			<div className='m-4'>
				<SectionCard size='full'>
					<div className='mb-4 flex justify-between'>
						<Input placeholder='Search books or authors...' className='mb-4 w-sm' value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} />
						<div className='flex gap-2'>
							<Select value={selectedStatus} onValueChange={(value) => setSelectedStatus(value as StatusFilter)}>
								<SelectTrigger className='w-full max-w-48'><SelectValue placeholder='ステータス' /></SelectTrigger>
								<SelectContent>
									<SelectItem value='all'>すべて</SelectItem>
									<SelectItem value='reading'>読書中</SelectItem>
									<SelectItem value='completed'>読了</SelectItem>
									<SelectItem value='unread'>未読</SelectItem>
								</SelectContent>
							</Select>
							<Select value={selectedSort} onValueChange={setSelectedSort}>
								<SelectTrigger className='w-full max-w-48'><SelectValue placeholder='最新順' /></SelectTrigger>
								<SelectContent>
									<SelectItem value='newest'>最新順</SelectItem>
									<SelectItem value='oldest'>古い順</SelectItem>
								</SelectContent>
							</Select>
						</div>
					</div>

					{isError ? (
						<p className='text-destructive'>書籍一覧の取得に失敗しました。</p>
					) : (
						<div className='overflow-x-auto'>
							<BookTable
								key={`${searchQuery}-${selectedSort}-${selectedStatus}`}
								books={books}
								itemsPerPage={itemsPerPage}
								sort={selectedSort}
								searchQuery={searchQuery}
								isLoading={isLoading}
								status={selectedStatus}
							/>
						</div>
					)}
				</SectionCard>
			</div>
		</>
	);
}

export default Home;
