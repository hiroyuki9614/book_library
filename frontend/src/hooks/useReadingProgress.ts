import { useQuery } from '@tanstack/react-query';
import readingProgressApiMock from '@/mocks/readingProgressApiMock';

export default function useReadingProgress() {
	const {
		data: readingProgress = [],
		isLoading,
		error,
	} = useQuery({
		queryKey: ['readingProgress'],
		queryFn: () => readingProgressApiMock(),
	});

	return { readingProgress, isLoading, error };
}
