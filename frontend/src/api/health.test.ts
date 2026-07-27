import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { getHealth } from './health';

describe('getHealth', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  test('calls /health and returns parsed JSON on 200', async () => {
    const mockBody = { status: 'ok', service: 'belib-api' };

    const response = new Response(JSON.stringify(mockBody), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });

    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response as unknown as Response);

    const res = await getHealth();

    expect(fetchMock).toHaveBeenCalled();
    expect(res).toEqual(mockBody);
  });

  test('throws when HTTP status is not ok', async () => {
    const response = new Response('Not Found', { status: 404 });
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response as unknown as Response);

    await expect(getHealth()).rejects.toThrow();
  });
});
