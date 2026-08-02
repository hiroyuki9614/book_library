import { describe, it, expect, vi } from 'vitest';
import { createInitialAdmin } from '../src/lib/initialAdmin.js';

function makePrismaMock(overrides: any = {}) {
  const defaultMock: any = {
    role: { upsert: vi.fn().mockResolvedValue({ id: 1, name: 'admin' }) },
    user: {
      findUnique: vi.fn().mockResolvedValue(null),
      create: vi.fn().mockImplementation(async ({ data }) => ({ id: 10, ...data })),
    },
    account: {
      findFirst: vi.fn().mockResolvedValue(null),
      create: vi.fn().mockResolvedValue({ id: 100 }),
      update: vi.fn().mockResolvedValue({}),
    },
  };

  return { ...defaultMock, ...overrides };
}

describe('createInitialAdmin', () => {
  it('fails when missing required values', async () => {
    const prisma = makePrismaMock();
    await expect(createInitialAdmin(prisma, { email: '', name: '', password: '' })).rejects.toThrow();
  });

  it('fails when password too short', async () => {
    const prisma = makePrismaMock();
    await expect(createInitialAdmin(prisma, { email: 'a@b.com', name: 'x', password: 'short' })).rejects.toThrow(/8/);
  });

  it('creates admin role and user and account', async () => {
    const prisma: any = makePrismaMock();
    const res = await createInitialAdmin(prisma, { email: 'admin@example.com', name: 'Admin', password: 'LongEnough1' });
    expect(res.created).toBe(true);
    expect(prisma.role.upsert).toHaveBeenCalled();
    expect(prisma.user.create).toHaveBeenCalled();
    expect(prisma.account.create).toHaveBeenCalled();
  });

  it('does not elevate existing non-admin user', async () => {
    const prisma = makePrismaMock({ user: { findUnique: vi.fn().mockResolvedValue({ id: 5, roleId: 2 }) } });
    await expect(createInitialAdmin(prisma, { email: 'u@e.com', name: 'U', password: 'LongEnough1' })).rejects.toThrow(/non-admin/);
  });

  it('is idempotent for existing admin user', async () => {
    const prisma = makePrismaMock({ user: { findUnique: vi.fn().mockResolvedValue({ id: 7, roleId: 1 }) } });
    const res = await createInitialAdmin(prisma, { email: 'admin@example.com', name: 'Admin', password: 'LongEnough1' });
    expect(res.created).toBe(false);
    expect(prisma.account.findFirst).toHaveBeenCalled();
  });
});
