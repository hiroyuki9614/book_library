type ReadingSaveStatusProps = {
	status: 'unknown' | 'saved' | 'saving' | 'error';
	retry: () => void;
};

export default function ReadingSaveStatus({ status, retry }: ReadingSaveStatusProps) {
	const detail =
		status === 'error'
			? '読書位置を保存できませんでした。'
			: status === 'saving'
				? '読書位置を保存中…'
				: status === 'saved'
					? '読書位置を保存済み'
					: '読書位置の保存状態を確認できていません。';

	return (
		<div
			className='flex min-h-11 min-w-0 items-center gap-1 text-xs text-muted-foreground'
			role={status === 'error' ? 'alert' : 'status'}
		>
			{status === 'error' ? (
				<>
					<span aria-hidden='true' title={detail} className='shrink-0 whitespace-nowrap text-destructive'>保存失敗</span>
					<span className='sr-only'>{detail}</span>
					<button type='button' className='min-h-11 shrink-0 px-2 text-primary underline' onClick={retry}>
						再試行
					</button>
				</>
			) : (
				<>
					<span aria-hidden='true' title={detail} className='whitespace-nowrap'>
						{status === 'saving' ? '保存中…' : status === 'saved' ? '✓ 保存済み' : '未確認'}
					</span>
					<span className='sr-only'>{detail}</span>
				</>
			)}
		</div>
	);
}
