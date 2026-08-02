import { hashPassword as defaultHashPassword } from 'better-auth/crypto';
function validateEmail(email) {
    return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email);
}
export async function createInitialAdmin(prisma, params, opts) {
    const hashPassword = opts?.hashPassword ?? defaultHashPassword;
    if (!params.email || !params.name || !params.password) {
        throw new Error('INITIAL_ADMIN_EMAIL / INITIAL_ADMIN_NAME / INITIAL_ADMIN_PASSWORD are required');
    }
    if (!validateEmail(params.email)) {
        throw new Error('Invalid email format');
    }
    if (params.password.length < 8) {
        throw new Error('Password must be at least 8 characters long');
    }
    // Ensure admin role exists
    const adminRole = await prisma.role.upsert({
        where: { name: 'admin' },
        update: {},
        create: { name: 'admin' },
    });
    // Check for existing user by email (case-insensitive behavior assumed handled by DB)
    const existingUser = await prisma.user.findUnique({ where: { email: params.email } });
    if (existingUser) {
        // If already admin, ensure credential account exists and return idempotently
        if (existingUser.roleId === adminRole.id) {
            const existingAccount = await prisma.account.findFirst({ where: { userId: existingUser.id, providerId: 'credential' } });
            const passwordHash = await hashPassword(params.password);
            const accountData = {
                accountId: String(existingUser.id),
                providerId: 'credential',
                password: passwordHash,
                userId: existingUser.id,
            };
            if (existingAccount) {
                await prisma.account.update({ where: { id: existingAccount.id }, data: accountData });
            }
            else {
                await prisma.account.create({ data: accountData });
            }
            return { created: false, userId: existingUser.id };
        }
        // Existing non-admin user: refuse to change role
        throw new Error('A non-admin user with this email already exists; refusing to elevate to admin');
    }
    // Create user with admin role
    const user = await prisma.user.create({
        data: {
            email: params.email,
            name: params.name,
            roleId: adminRole.id,
        },
    });
    const passwordHash = await hashPassword(params.password);
    await prisma.account.create({
        data: {
            accountId: String(user.id),
            providerId: 'credential',
            password: passwordHash,
            userId: user.id,
        },
    });
    return { created: true, userId: user.id };
}
export default createInitialAdmin;
