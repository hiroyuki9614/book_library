export function getApiBaseUrl(): string | undefined {
  return import.meta.env.VITE_API_BASE_URL as string | undefined;
}

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const baseUrl = getApiBaseUrl();

  if (!baseUrl) {
    throw new Error('VITE_API_BASE_URL is not configured');
  }

  const response = await fetch(`${baseUrl}${path}`, {
    ...options,
    credentials: 'include',
    headers: {
      ...options?.headers,
    },
  });

  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new Error('Failed to parse JSON response from API');
  }
}
