import { useMutation } from '@tanstack/react-query';
import userApiMock from '@/mocks/userApiMock';

export default function useUser() {
	return useMutation({
		mutationFn: (id: number) => userApiMock(id),
	});
}
