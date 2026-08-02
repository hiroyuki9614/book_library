import { auth } from '../../lib/auth.js';
import { prisma } from '../../lib/prisma.js';
export const getMe = async (c) => {
    const session = await auth.api.getSession({
        headers: c.req.raw.headers,
    });
    if (!session) {
        return c.json({
            message: 'Authentication required',
            code: 'UNAUTHORIZED',
        }, 401);
    }
    const userId = Number(session.user.id);
    if (!Number.isInteger(userId)) {
        return c.json({
            message: 'Authentication required',
            code: 'UNAUTHORIZED',
        }, 401);
    }
    const user = await prisma.user.findFirst({
        where: {
            id: userId,
            deletedAt: null,
        },
        select: {
            id: true,
            name: true,
            email: true,
            role: {
                select: {
                    name: true,
                },
            },
        },
    });
    if (!user) {
        return c.json({
            message: 'Authentication required',
            code: 'UNAUTHORIZED',
        }, 401);
    }
    return c.json({
        id: user.id,
        name: user.name,
        email: user.email,
        role: user.role.name,
    });
};
