// SectionCard.tsx

import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';

interface SectionCardProps {
	children: React.ReactNode;
}

export function SectionCard({ children }: SectionCardProps) {
	return (
		<Card className='w-full bg-card'>
			<CardHeader>
				<CardTitle>Card Title</CardTitle>

				<CardDescription>Card Description</CardDescription>
			</CardHeader>

			<CardContent>{children}</CardContent>

			<CardFooter>
				<CardAction>
					<button className='rounded bg-primary px-4 py-2 text-white'>Action</button>
				</CardAction>
			</CardFooter>
		</Card>
	);
}
