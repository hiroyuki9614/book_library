import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Progress } from '@/components/ui/progress';
import { Star, MoreHorizontal } from 'lucide-react';

export function BookTable() {
	return (
		<Table>
			<TableHeader>
				<TableRow>
					<TableHead></TableHead>
					<TableHead>タイトル</TableHead>
					<TableHead>著者</TableHead>
					<TableHead>ジャンル</TableHead>
					<TableHead>ステータス</TableHead>
					<TableHead>進捗</TableHead>
					<TableHead></TableHead>
				</TableRow>
			</TableHeader>

			<TableBody>
				<TableRow>
					<TableCell>
						<div className='h-20 w-16 bg-muted' />
					</TableCell>

					<TableCell className='font-medium'>タイトル</TableCell>
					<TableCell>著者</TableCell>
					<TableCell>ジャンル</TableCell>
					<TableCell>ステータス</TableCell>

					<TableCell>
						<Progress value={50} className='w-24' />
					</TableCell>

					<TableCell>
						<div className='flex items-center gap-2'>
							<button>
								<MoreHorizontal className='h-4 w-4' />
							</button>
							<button>
								<Star className='h-5 w-5' />
							</button>
						</div>
					</TableCell>
				</TableRow>
			</TableBody>
		</Table>
	);
}
