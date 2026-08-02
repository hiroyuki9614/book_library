import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { getE2EConfig } from '../src/e2e/e2eEnv.js';
import { runInitialAdminSetup } from '../src/e2e/runInitialAdminSetup.js';

export default function globalSetup() {
	const config = getE2EConfig(process.env);
	const frontendDirectory = path.dirname(fileURLToPath(import.meta.url));
	const backendDirectory = path.resolve(frontendDirectory, '../../backend');
	runInitialAdminSetup(config, backendDirectory);
}
