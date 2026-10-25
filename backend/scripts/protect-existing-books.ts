import 'dotenv/config';
import { applyExistingBookProtection, planExistingBookProtection } from '../src/lib/portfolioProtection.js';
import { loadPortfolioGate, verifyLivePortfolioGate } from '../src/lib/portfolioGate.js';
import { reportPortfolioFailure } from '../src/lib/portfolioRuntime.js';

async function main() {
	const args = process.argv.slice(2);
	const execute = args.includes('--execute');
	const gate = await loadPortfolioGate(args, execute);
	const { prisma } = await import('../src/lib/prisma.js');
	try {
		await verifyLivePortfolioGate(prisma, gate, false);
		const plan = await planExistingBookProtection(prisma, gate.snapshot);
		console.log(JSON.stringify({ mode: execute ? 'execute' : 'dry-run', snapshotSha256: gate.snapshotSha256, ...plan }, null, 2));
		if (!execute) return;
		const result = await applyExistingBookProtection(prisma, gate.snapshot);
		console.log(JSON.stringify({ success: true, ...result }, null, 2));
	} finally {
		await prisma.$disconnect();
	}
}

main().catch(reportPortfolioFailure);
