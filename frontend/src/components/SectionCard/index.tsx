import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import clsx from 'clsx';

interface SectionCardProps {
	children: React.ReactNode;
	pageTitle?: string;
	pageDescription?: string;
	className?: string;
	size?: 'small' | 'medium' | 'large' | 'full';
}

function SectionCard({ size, children, pageTitle, pageDescription, className }: SectionCardProps) {
	const sizeClasses = {
		small: 'max-w-sm',
		medium: 'max-w-md',
		large: 'max-w-lg',
		full: 'w-full',
	};

	return (
		<Card className={clsx('bg-card', size ? sizeClasses[size] : 'w-full', className)}>
			<CardHeader>
				{pageTitle && <CardTitle>{pageTitle}</CardTitle>}
				{pageDescription && <CardDescription>{pageDescription}</CardDescription>}
			</CardHeader>
			<CardContent>{children}</CardContent>
		</Card>
	);
}

export default SectionCard;
