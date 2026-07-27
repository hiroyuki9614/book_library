import 'dotenv/config';
import { Pool } from 'pg';
import { PrismaPg } from '@prisma/adapter-pg';
import { hashPassword } from 'better-auth/crypto';
import { PrismaClient } from './generated/prisma/client.js';
import { bookSeeds, categorySeeds } from './seedData.js';

const connectionString = process.env.DATABASE_URL;

if (!connectionString) {
	throw new Error('DATABASE_URL is not defined');
}

const pool = new Pool({ connectionString });
const adapter = new PrismaPg(pool);
const prisma = new PrismaClient({ adapter });

const userPassword = process.env.SEED_USER_PASSWORD ?? 'DummyPass123!';
const adminPassword = process.env.SEED_ADMIN_PASSWORD ?? 'AdminPass123!';

async function upsertCredentialAccount(userId: number, password: string) {
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

	await upsertCredentialAccount(user.id, userPassword);
	await upsertCredentialAccount(admin.id, adminPassword);

	const categories = new Map<string, number>();

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

	const books = new Map<string, number>();

	for (const bookSeed of bookSeeds) {
		const categoryId = categories.get(bookSeed.categoryName);

		if (!categoryId) {
			throw new Error(`Category not found: ${bookSeed.categoryName}`);
		}

		const authorName = 'authorName' in bookSeed ? bookSeed.authorName : null;
		const publisher = 'publisher' in bookSeed ? bookSeed.publisher : null;
		const data = {
			title: bookSeed.title,
			authorName,
			publishedAt: null,
			publisher,
			description: null,
			categoryId,
			pageTurnDirection: 'ltr',
			deletedAt: null,
		};
		const existingBook = await prisma.book.findFirst({
			where: {
				title: bookSeed.title,
				authorName,
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

	console.log(
		`Seed completed: 2 credential users, ${categorySeeds.length} categories, ${bookSeeds.length} personal library books, ${sampleReadingInfos.length} reading infos`,
	);
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
