import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

interface MiniCardProps {
	children: React.ReactNode;
}

export function MiniCard({ children }: MiniCardProps) {
	return (
		<Card className='w-[20%] bg-card'>
			<CardHeader>
				<CardTitle>Card Title</CardTitle>

				<CardDescription>Card Description</CardDescription>
			</CardHeader>

			<CardContent>{children}</CardContent>
		</Card>
	);
}
