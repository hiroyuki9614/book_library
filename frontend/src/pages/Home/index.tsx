import { BookTable } from '@/components/BookTable';
import { MiniCard } from '@/components/MiniCard';
import SectionCard from '@/components/SectionCard';
import { Input } from '@/components/ui/input';
import useBooks from '@/hooks/useBooks';
import useReadingProgress from '@/hooks/useReadingProgress';

function Home() {
	const { books } = useBooks();
	const { readingProgress } = useReadingProgress();
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
				<BookTable books={books} readingProgress={readingProgress} />
			</SectionCard>
		</>
	);
}

export default Home;
