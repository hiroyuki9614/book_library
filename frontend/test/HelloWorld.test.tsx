import { expect, test, describe } from 'vitest';
import { render } from 'vitest-browser-react';
import HelloWorld from './HelloWorld.tsx';

describe('HelloWorld Component', () => {
	test('renders name', async () => {
		const { getByText } = await render(<HelloWorld name='Vitest' />);
		await expect.element(getByText('Hello Vitest!')).toBeInTheDocument();
	});
});
