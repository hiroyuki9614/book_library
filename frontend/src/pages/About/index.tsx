import { Link } from 'react-router-dom';

function About() {
	return (
		<main className='mx-auto max-w-4xl space-y-8 p-6'>
			<section>
				<h1 className='text-3xl font-bold'>BeLibについて</h1>
				<p className='mt-3 text-muted-foreground'>BeLibは、管理者が登録したEPUB/PDFをブラウザで読み、端末をまたいで読書位置を再開できる個人向けWebライブラリです。</p>
			</section>
			<section className='grid gap-4 md:grid-cols-3'>
				<div className='rounded-lg border p-4'><h2 className='font-semibold'>保護配信</h2><p className='mt-2 text-sm text-muted-foreground'>非公開ストレージから、認証とロール権限を確認した利用者だけへ書籍を配信します。</p></div>
				<div className='rounded-lg border p-4'><h2 className='font-semibold'>読書体験</h2><p className='mt-2 text-sm text-muted-foreground'>PDFページとEPUB CFIをユーザーごとに保存し、前回の位置から再開します。</p></div>
				<div className='rounded-lg border p-4'><h2 className='font-semibold'>権限分離</h2><p className='mt-2 text-sm text-muted-foreground'>一般ユーザーは公開デモ作品だけを閲覧でき、管理機能と個人蔵書にはアクセスできません。</p></div>
			</section>
			<section>
				<h2 className='text-xl font-semibold'>技術構成</h2>
				<p className='mt-2 text-sm text-muted-foreground'>React / TypeScript / Hono / Prisma / PostgreSQL / Better Auth / Cloudflare R2。VitestとPlaywrightで認証・認可・読書位置の経路を検証しています。</p>
			</section>
			<Link to='/' className='inline-block text-primary'>本棚に戻る</Link>
		</main>
	);
}

export default About;
