import { useQuery } from '@tanstack/react-query';
import readingProgressesApiMock from '@/mocks/readingProgressesApiMock';

export default function useReadingProgresses() {
	const {
		data: readingProgresses = [],
		isLoading,
		error,
	} = useQuery({
		queryKey: ['readingProgress'],
		queryFn: () => readingProgressesApiMock(),
	});

	return { readingProgresses, isLoading, error };
}
