import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

interface MiniCardProps {
	title: string;
	description?: string;
	children: React.ReactNode;
	color?: 'bg-primary/20' | 'bg-blue-500/20' | 'bg-red-500/20' | 'bg-green-500/20' | 'bg-purple-500/20';
}

export function MiniCard({ title, description, children, color }: MiniCardProps) {
	return (
		<Card className='flex-1 bg-card'>
			<CardContent className='flex items-center gap-4 p-6'>
				<div className={`rounded-lg p-3 ${color || 'bg-primary/10'}`}></div>

				<div className='flex flex-col'>
					<CardTitle>{title}</CardTitle>

					{description && <CardDescription>{description}</CardDescription>}

					<div className='mt-2'>{children}</div>
				</div>
			</CardContent>
		</Card>
	);
}
