import 'dotenv/config';
import { readDemoUserConfig } from '../src/lib/demoUserConfig.js';
import { createDemoUser } from '../src/lib/demoUser.js';
import { loadPortfolioGate, verifyLivePortfolioGate } from '../src/lib/portfolioGate.js';
import { reportPortfolioFailure } from '../src/lib/portfolioRuntime.js';

async function main() {
	const args = process.argv.slice(2);
	const execute = args.includes('--execute');
	const gate = await loadPortfolioGate(args, execute);
	const config = readDemoUserConfig(process.env);
	const { prisma } = await import('../src/lib/prisma.js');
	try {
		await verifyLivePortfolioGate(prisma, gate, true);
		const result = await createDemoUser(prisma, config, { dryRun: !execute });
		console.log(JSON.stringify({ mode: execute ? 'execute' : 'dry-run', created: result.created }, null, 2));
	} finally {
		await prisma.$disconnect();
	}
}

main().catch(reportPortfolioFailure);
