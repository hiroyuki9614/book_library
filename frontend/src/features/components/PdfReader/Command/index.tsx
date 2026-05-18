type ResolvedCommand = {
	visible: boolean;
	disabled: boolean;
	execute: () => void;
	label: string;
	shortcuts?: string[];
};

type CommandButtonProps = {
	resolved: ResolvedCommand | null;
	state: {
		currentPage: number;
		totalPages: number;
	};
	pageInput: number | string;
	setPageInput: (value: number | string) => void;
	handleGoToPage: (event: React.FormEvent<HTMLFormElement>) => void;
	prevResolved: ResolvedCommand | null;
	nextResolved: ResolvedCommand | null;
};

const CommandButton = ({ prevResolved, nextResolved, state, pageInput, setPageInput, handleGoToPage }: CommandButtonProps) => {
	if (!prevResolved || !prevResolved.visible) return null;

	return (
		<div className='flex items-center gap-4'>
			<button
				onClick={prevResolved.execute}
				disabled={prevResolved.disabled}
				className='inline-flex h-8 w-8 items-center justify-center rounded-md bg-white text-gray-600 shadow-sm ring-1 ring-gray-300 transition-all hover:bg-gray-50 hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-gray-700 dark:text-gray-300 dark:ring-gray-600 dark:hover:bg-gray-600 dark:hover:text-gray-100'
				title={`${prevResolved.label} (${prevResolved.shortcuts?.[0] || ''})`}
			>
				{prevResolved.label}
			</button>

			<form onSubmit={handleGoToPage} className='flex items-center gap-2'>
				<span className='text-xs font-medium uppercase tracking-wide text-gray-600 dark:text-gray-300'>Page</span>

				<input
					type='number'
					value={pageInput}
					onChange={(event) => setPageInput(event.target.value)}
					min={1}
					max={state.totalPages}
					className='h-8 w-14 rounded-md border-0 bg-white px-2 text-center font-mono text-sm font-medium text-gray-700 shadow-sm ring-1 ring-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:text-gray-300 dark:ring-gray-600'
				/>

				<span className='text-xs font-medium text-gray-600 dark:text-gray-300'>of {state.totalPages}</span>
			</form>
			<button
				onClick={nextResolved?.execute}
				disabled={nextResolved?.disabled}
				className='inline-flex h-8 w-8 items-center justify-center rounded-md bg-white text-gray-600 shadow-sm ring-1 ring-gray-300 transition-all hover:bg-gray-50 hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-gray-700 dark:text-gray-300 dark:ring-gray-600 dark:hover:bg-gray-600 dark:hover:text-gray-100'
				title={`${nextResolved?.label} (${nextResolved?.shortcuts?.[0] || ''})`}
			>
				{nextResolved?.label}
			</button>
		</div>
	);
};

export default CommandButton;
