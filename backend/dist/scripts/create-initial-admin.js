import 'dotenv/config';
import { Pool } from 'pg';
import { PrismaPg } from '@prisma/adapter-pg';
import { PrismaClient } from '../prisma/generated/prisma/client.js';
import createInitialAdmin from '../src/lib/initialAdmin.js';
const connectionString = process.env.DATABASE_URL;
if (!connectionString) {
    console.error('DATABASE_URL is not defined');
    process.exit(1);
}
function getArg(name) {
    // allow env or CLI --name=value
    const env = process.env[name];
    if (env)
        return env;
    const arg = process.argv.find((a) => a.startsWith(`--${name}=`));
    if (arg)
        return arg.split('=')[1];
    return undefined;
}
async function main() {
    const pool = new Pool({ connectionString });
    const adapter = new PrismaPg(pool);
    const prisma = new PrismaClient({ adapter });
    try {
        const email = getArg('INITIAL_ADMIN_EMAIL');
        const name = getArg('INITIAL_ADMIN_NAME');
        const password = getArg('INITIAL_ADMIN_PASSWORD');
        if (!email || !name || !password) {
            console.error('Missing required values. Provide INITIAL_ADMIN_EMAIL, INITIAL_ADMIN_NAME, INITIAL_ADMIN_PASSWORD (env or --FLAG=value)');
            process.exit(2);
        }
        const result = await createInitialAdmin(prisma, { email, name, password });
        if (result.created) {
            console.log('Initial admin created.');
        }
        else {
            console.log('Initial admin already existed; credential ensured.');
        }
    }
    catch (error) {
        console.error('Error creating initial admin:', error.message ?? error);
        process.exit(3);
    }
    finally {
        await prisma.$disconnect();
        await pool.end();
    }
}
main();
