type ResolvedCommand = {
	visible: boolean;
	disabled: boolean;
	execute: () => void;
	label: string;
	shortcuts?: string[];
};

type CommandButtonProps = {
	state: {
		currentPage: number;
		totalPages: number;
	};
	pageInput: string;
	setPageInput: (value: string) => void;
	handleGoToPage: (event: React.FormEvent<HTMLFormElement>) => void;
	prevResolved: ResolvedCommand | null;
	nextResolved: ResolvedCommand | null;
};

const CommandButton = ({ prevResolved, nextResolved, state, pageInput, setPageInput, handleGoToPage }: CommandButtonProps) => {
	if (!prevResolved || !prevResolved.visible) return null;
	const percentage = state.totalPages > 0 ? Math.round((state.currentPage / state.totalPages) * 1000) / 10 : null;

	return (
		<div className='flex flex-wrap items-center justify-center gap-2'>
			<button
				onClick={prevResolved.execute}
				disabled={prevResolved.disabled}
				aria-label='前のページへ'
				className='inline-flex size-11 shrink-0 items-center justify-center rounded-md bg-white text-lg text-gray-600 shadow-sm ring-1 ring-gray-300 transition-all hover:bg-gray-50 hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-gray-700 dark:text-gray-300 dark:ring-gray-600 dark:hover:bg-gray-600 dark:hover:text-gray-100'
				title={`${prevResolved.label} (${prevResolved.shortcuts?.[0] || ''})`}
			>
				{prevResolved.label}
			</button>

			<form onSubmit={handleGoToPage} className='flex items-center gap-2'>
				<span className='text-xs font-medium uppercase tracking-wide text-gray-600 dark:text-gray-300'>Page</span>
				<input
					aria-label='ページ番号'
					type='number'
					value={pageInput}
					onChange={(event) => setPageInput(event.target.value)}
					min={1}
					max={state.totalPages}
					className='h-11 w-16 rounded-md border-0 bg-white px-2 text-center font-mono text-sm font-medium text-gray-700 shadow-sm ring-1 ring-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-700 dark:text-gray-300 dark:ring-gray-600'
				/>
				<span className='text-xs font-medium text-gray-600 dark:text-gray-300'>/ {state.totalPages}</span>
			</form>
			{percentage !== null && (
				<output aria-live='polite' className='min-w-32 text-center text-xs font-medium text-gray-600 dark:text-gray-300'>
					{state.currentPage} / {state.totalPages} ページ {percentage}%
				</output>
			)}
			<button
				onClick={nextResolved?.execute}
				disabled={nextResolved?.disabled}
				aria-label='次のページへ'
				className='inline-flex size-11 shrink-0 items-center justify-center rounded-md bg-white text-lg text-gray-600 shadow-sm ring-1 ring-gray-300 transition-all hover:bg-gray-50 hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-gray-700 dark:text-gray-300 dark:ring-gray-600 dark:hover:bg-gray-600 dark:hover:text-gray-100'
				title={`${nextResolved?.label} (${nextResolved?.shortcuts?.[0] || ''})`}
			>
				{nextResolved?.label}
			</button>
		</div>
	);
};

export default CommandButton;
