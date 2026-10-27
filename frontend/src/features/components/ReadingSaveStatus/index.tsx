export default function ReadingSaveStatus({ status, retry }: { status: 'saved' | 'saving' | 'error'; retry: () => void }) {
	return (
		<div className='text-xs text-muted-foreground' role={status === 'error' ? 'alert' : 'status'}>
			{status === 'error' ? (
				<>
					読書位置を保存できませんでした。
					<button type='button' className='min-h-11 px-2 text-primary underline' onClick={retry}>
						再試行
					</button>
				</>
			) : status === 'saving' ? (
				'読書位置を保存中…'
			) : (
				'読書位置を保存済み'
			)}
		</div>
	);
}
