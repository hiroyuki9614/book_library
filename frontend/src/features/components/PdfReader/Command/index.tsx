import { Button } from '@/components/ui/button';

type ResolvedCommand = {
	visible: boolean;
	disabled: boolean;
	execute: () => void;
	label: string;
	shortcuts?: string[];
};

type CommandButtonProps = {
	pageTurnDirection?: 'ltr' | 'rtl';
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

const CommandButton = ({ prevResolved, nextResolved, state, pageInput, setPageInput, handleGoToPage, pageTurnDirection = 'ltr' }: CommandButtonProps) => {
	if (!prevResolved || !prevResolved.visible) return null;
	const percentage = state.totalPages > 0 ? Math.round((state.currentPage / state.totalPages) * 100) : null;

	return (
		<div style={{flexDirection:pageTurnDirection === 'rtl' ? 'row-reverse' : 'row'}} className='flex flex-wrap items-center justify-center gap-2'>
			<Button
				type='button'
				variant='outline'
				onClick={prevResolved.execute}
				disabled={prevResolved.disabled}
				aria-label='前のページへ'
				className='size-11 shrink-0 rounded-full px-0 text-2xl'
				title={`${prevResolved.label} (${prevResolved.shortcuts?.[0] || ''})`}
			>
				<span aria-hidden='true' className='block -translate-y-px leading-none'>{prevResolved.label === '<' ? '‹' : prevResolved.label === '>' ? '›' : prevResolved.label}</span>
			</Button>

			<form onSubmit={handleGoToPage} className='flex items-center gap-2'>
				<span className='text-xs font-medium uppercase tracking-wide text-muted-foreground'>Page</span>
				<input
					aria-label='ページ番号'
					type='number'
					value={pageInput}
					onChange={(event) => setPageInput(event.target.value)}
					min={1}
					max={state.totalPages}
					className='h-11 w-16 rounded-md border border-input bg-white px-2 text-center font-mono text-sm font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
				/>
				<span className='text-xs font-medium text-muted-foreground'>/ {state.totalPages}</span>
			</form>
			{percentage !== null && (
				<output aria-live='polite' className='min-w-32 text-center text-xs font-medium text-muted-foreground'>
					{state.currentPage} / {state.totalPages} ページ {percentage}%
				</output>
			)}
			<Button
				type='button'
				variant='outline'
				onClick={nextResolved?.execute}
				disabled={nextResolved?.disabled}
				aria-label='次のページへ'
				className='size-11 shrink-0 rounded-full px-0 text-2xl'
				title={`${nextResolved?.label} (${nextResolved?.shortcuts?.[0] || ''})`}
			>
				<span aria-hidden='true' className='block -translate-y-px leading-none'>{nextResolved?.label === '<' ? '‹' : nextResolved?.label === '>' ? '›' : nextResolved?.label}</span>
			</Button>
		</div>
	);
};

export default CommandButton;
