import { BookTable } from '@/components/BookTable';
import { MiniCard } from '@/components/MiniCard';
import SectionCard from '@/components/SectionCard';
import useBooks from '@/hooks/useBooks';
import useReadingProgresses from '@/hooks/useReadingProgresses';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useState } from 'react';

function Home() {
	const { books, isLoading } = useBooks();
	const { readingProgresses } = useReadingProgresses();
	const [selectedSort, setSelectedSort] = useState('newest');
	const [searchQuery, setSearchQuery] = useState('');

	const itemsPerPage = 5;
	return (
		<>
			<div className='m-4 flex gap-4'>
				<MiniCard>
					<p>Mini Card Content</p>
				</MiniCard>

				<MiniCard>
					<p>Mini Card Content</p>
				</MiniCard>

				<MiniCard>
					<p>Mini Card Content</p>
				</MiniCard>

				<MiniCard>
					<p>Mini Card Content</p>
				</MiniCard>

				<MiniCard>
					<p>Mini Card Content</p>
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
					<BookTable books={books} readingProgresses={readingProgresses} itemsPerPage={itemsPerPage} sort={selectedSort} searchQuery={searchQuery} isLoading={isLoading} />
				</SectionCard>
			</div>
		</>
	);
}

export default Home;
