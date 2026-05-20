import { BookTable } from '@/components/BookTable';
import { MiniCard } from '@/components/MiniCard';
import SectionCard from '@/components/SectionCard';
import { Input } from '@/components/ui/input';
import useBooks from '@/hooks/useBooks';

function Home() {
	const { books } = useBooks();
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
				<BookTable books={books} />
			</SectionCard>
		</>
	);
}

export default Home;
