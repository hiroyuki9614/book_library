import { BookTable } from '@/components/BookTable';
import { MiniCard } from '@/components/MiniCard';
import { SectionCard } from '@/components/SectionCard';
import { Input } from '@/components/ui/input';

function Home() {
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
				<Input />

				<BookTable />
			</SectionCard>
		</>
	);
}

export default Home;
