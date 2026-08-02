import 'dotenv/config';
import { Pool } from 'pg';
import { PrismaPg } from '@prisma/adapter-pg';
import { hashPassword } from 'better-auth/crypto';
import { PrismaClient } from './generated/prisma/client.js';
const connectionString = process.env.DATABASE_URL;
if (!connectionString) {
    throw new Error('DATABASE_URL is not defined');
}
const pool = new Pool({ connectionString });
const adapter = new PrismaPg(pool);
const prisma = new PrismaClient({ adapter });
const userPassword = process.env.SEED_USER_PASSWORD ?? 'DummyPass123!';
const adminPassword = process.env.SEED_ADMIN_PASSWORD;
const categorySeeds = [
    { name: '小説', displayOrder: 1 },
    { name: '技術書', displayOrder: 2 },
    { name: 'ビジネス', displayOrder: 3 },
    { name: '歴史', displayOrder: 4 },
    { name: 'その他', displayOrder: 5 },
];
const bookSeeds = [
    {
        title: 'ブラウザで読むサンプル小説',
        authorName: 'BeLib Sample Author',
        publishedAt: new Date('2024-01-15T00:00:00.000Z'),
        publisher: 'BeLib Press',
        description: 'EPUBリーダーと右開き表示を確認するためのサンプル小説です。',
        categoryName: '小説',
        pageTurnDirection: 'rtl',
    },
    {
        title: 'TypeScript API開発入門',
        authorName: 'BeLib Development Team',
        publishedAt: new Date('2025-03-10T00:00:00.000Z'),
        publisher: 'BeLib Press',
        description: 'TypeScriptとHonoを使ったAPI開発を学ぶためのサンプル技術書です。',
        categoryName: '技術書',
        pageTurnDirection: 'ltr',
    },
    {
        title: 'PostgreSQLデータベース設計',
        authorName: 'BeLib Development Team',
        publishedAt: new Date('2025-06-20T00:00:00.000Z'),
        publisher: 'BeLib Press',
        description: 'リレーショナルデータベース設計を確認するためのサンプル技術書です。',
        categoryName: '技術書',
        pageTurnDirection: 'ltr',
    },
    {
        title: 'チーム開発の基本',
        authorName: 'BeLib Development Team',
        publishedAt: new Date('2025-09-01T00:00:00.000Z'),
        publisher: 'BeLib Press',
        description: 'チームでソフトウェアを開発する際の基本をまとめたサンプル書籍です。',
        categoryName: 'ビジネス',
        pageTurnDirection: 'ltr',
    },
];
async function upsertCredentialAccount(userId, password) {
    const passwordHash = await hashPassword(password);
    const existingAccount = await prisma.account.findFirst({
        where: {
            userId,
            providerId: 'credential',
        },
    });
    const data = {
        accountId: String(userId),
        providerId: 'credential',
        password: passwordHash,
    };
    if (existingAccount) {
        await prisma.account.update({
            where: { id: existingAccount.id },
            data,
        });
        return;
    }
    await prisma.account.create({
        data: {
            ...data,
            userId,
        },
    });
}
async function main() {
    const userRole = await prisma.role.upsert({
        where: { name: 'user' },
        update: {},
        create: {
            name: 'user',
        },
    });
    const adminRole = await prisma.role.upsert({
        where: { name: 'admin' },
        update: {},
        create: {
            name: 'admin',
        },
    });
    const user = await prisma.user.upsert({
        where: { email: 'dummy@example.com' },
        update: {
            name: 'Dummy User',
            roleId: userRole.id,
            deletedAt: null,
        },
        create: {
            email: 'dummy@example.com',
            name: 'Dummy User',
            roleId: userRole.id,
        },
    });
    // For safety, seed does not create an admin when ADMIN password is not explicitly provided.
    // Use the dedicated `create-initial-admin` script to create a real admin.
    await upsertCredentialAccount(user.id, userPassword);
    if (adminPassword) {
        // If someone explicitly set SEED_ADMIN_PASSWORD in environment, create or update the admin.
        const admin = await prisma.user.upsert({
            where: { email: 'admin@example.com' },
            update: {
                name: 'Admin User',
                roleId: adminRole.id,
                deletedAt: null,
            },
            create: {
                email: 'admin@example.com',
                name: 'Admin User',
                roleId: adminRole.id,
            },
        });
        await upsertCredentialAccount(admin.id, adminPassword);
    }
    else {
        console.log('Skipping admin creation in seed (no SEED_ADMIN_PASSWORD provided)');
    }
    const categories = new Map();
    for (const categorySeed of categorySeeds) {
        const category = await prisma.category.upsert({
            where: { name: categorySeed.name },
            update: {
                displayOrder: categorySeed.displayOrder,
                isActive: true,
            },
            create: {
                ...categorySeed,
                isActive: true,
            },
        });
        categories.set(category.name, category.id);
    }
    const books = new Map();
    for (const bookSeed of bookSeeds) {
        const categoryId = categories.get(bookSeed.categoryName);
        if (!categoryId) {
            throw new Error(`Category not found: ${bookSeed.categoryName}`);
        }
        const data = {
            title: bookSeed.title,
            authorName: bookSeed.authorName,
            publishedAt: bookSeed.publishedAt,
            publisher: bookSeed.publisher,
            description: bookSeed.description,
            categoryId,
            pageTurnDirection: bookSeed.pageTurnDirection,
            deletedAt: null,
        };
        const existingBook = await prisma.book.findFirst({
            where: {
                title: bookSeed.title,
                authorName: bookSeed.authorName,
            },
        });
        const book = existingBook
            ? await prisma.book.update({ where: { id: existingBook.id }, data })
            : await prisma.book.create({ data });
        books.set(book.title, book.id);
    }
    const allBookIds = [...books.values()];
    const generalUserBookIds = allBookIds.slice(0, 3);
    for (const bookId of allBookIds) {
        await prisma.roleBookPermission.upsert({
            where: {
                roleId_bookId: { roleId: adminRole.id, bookId },
            },
            update: {},
            create: { roleId: adminRole.id, bookId },
        });
    }
    for (const bookId of generalUserBookIds) {
        await prisma.roleBookPermission.upsert({
            where: {
                roleId_bookId: { roleId: userRole.id, bookId },
            },
            update: {},
            create: { roleId: userRole.id, bookId },
        });
    }
    const sampleReadingInfos = [
        {
            bookId: generalUserBookIds[0],
            readStatus: 'completed',
            currentPosition: null,
        },
        {
            bookId: generalUserBookIds[1],
            readStatus: 'reading',
            currentPosition: 'epubcfi(/6/4[chapter2]!/4/2/8)',
        },
        {
            bookId: generalUserBookIds[2],
            readStatus: 'unread',
            currentPosition: null,
        },
    ];
    for (const readingInfo of sampleReadingInfos) {
        if (!readingInfo.bookId) {
            throw new Error('Book for reading info was not created');
        }
        await prisma.readingInfo.upsert({
            where: {
                userId_bookId: {
                    userId: user.id,
                    bookId: readingInfo.bookId,
                },
            },
            update: {
                readStatus: readingInfo.readStatus,
                currentPosition: readingInfo.currentPosition,
            },
            create: {
                userId: user.id,
                ...readingInfo,
            },
        });
    }
    console.log(`Seed completed: 2 credential users, ${categorySeeds.length} categories, ${bookSeeds.length} books, ${sampleReadingInfos.length} reading infos`);
}
main()
    .catch((error) => {
    console.error(error);
    process.exitCode = 1;
})
    .finally(async () => {
    await prisma.$disconnect();
    await pool.end();
});
