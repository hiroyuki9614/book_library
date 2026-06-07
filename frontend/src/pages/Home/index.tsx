import { BookTable } from '@/components/BookTable';
import { MiniCard } from '@/components/MiniCard';
import SectionCard from '@/components/SectionCard';
import useBooks from '@/hooks/useBooks';
import useReadingProgresses from '@/hooks/useReadingProgresses';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useState } from 'react';

function Home() {
	const { books, isLoading, isError } = useBooks();
	const { readingProgresses } = useReadingProgresses();
	const [selectedSort, setSelectedSort] = useState('newest');
	const [searchQuery, setSearchQuery] = useState('');

	const itemsPerPage = 5;
	return (
		<>
			<div className='m-4 flex gap-4'>
				<MiniCard>
					<p>総冊数</p>
					<p>{books.length}冊</p>
				</MiniCard>
				<MiniCard>
					<p>読書中</p>
					<p>{readingProgresses.filter((progress) => progress.status === 'reading').length}冊</p>
				</MiniCard>
				<MiniCard>
					<p>読了</p>
					<p>{readingProgresses.filter((progress) => progress.status === 'completed').length}冊</p>
				</MiniCard>
				<MiniCard>
					<p>未読</p>
					<p>{readingProgresses.filter((progress) => progress.status === 'unread').length}冊</p>
				</MiniCard>
				<MiniCard>
					<p>平均進捗</p>
					<p>{Math.round(readingProgresses.reduce((acc, progress) => acc + progress.progress, 0) / readingProgresses.length)}%</p>
				</MiniCard>
			</div>

			<div className='m-4'>
				<SectionCard size='full'>
					<div className='mb-4 flex justify-between'>
						<Input placeholder='Search books or authors...' className='mb-4 w-sm' value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} />
						<Select defaultValue={selectedSort} onValueChange={setSelectedSort}>
							<SelectTrigger className='w-full max-w-48'>
								<SelectValue placeholder='最新順' />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value='newest'>最新順</SelectItem>
								<SelectItem value='oldest'>古い順</SelectItem>
							</SelectContent>
						</Select>
					</div>
					{isError ? (
						<p className='text-destructive'>書籍一覧の取得に失敗しました。</p>
					) : (
						<BookTable books={books} readingProgresses={readingProgresses} itemsPerPage={itemsPerPage} sort={selectedSort} searchQuery={searchQuery} isLoading={isLoading} />
					)}
				</SectionCard>
			</div>
		</>
	);
}

export default Home;
