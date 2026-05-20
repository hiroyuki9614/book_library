import { BookTable } from '@/components/BookTable';
import { MiniCard } from '@/components/MiniCard';
import SectionCard from '@/components/SectionCard';
import useBooks from '@/hooks/useBooks';
import useReadingProgresses from '@/hooks/useReadingProgresses';

function Home() {
	const { books } = useBooks();
	const { readingProgresses } = useReadingProgresses();
	return (
		<>
			<div className='mb-4 flex gap-4'>
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

			<SectionCard>
				<BookTable books={books} readingProgresses={readingProgresses} />
			</SectionCard>
		</>
	);
}

export default Home;
