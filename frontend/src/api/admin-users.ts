type CreateUserParams = {
  name: string;
  email: string;
  password: string;
  role: 'user' | 'admin';
};

export async function createUser(params: CreateUserParams): Promise<{ ok: boolean; error?: string }> {
  const BASE = import.meta.env.VITE_API_BASE_URL ?? '';

  try {
    const res = await fetch(`${BASE}/api/admin/users`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    });

    if (res.ok) return { ok: true };

    let err = 'ユーザー作成に失敗しました';
    try {
      const body = await res.json();
      if (body?.error) err = body.error;
    } catch {}

    return { ok: false, error: err };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export default { createUser };
