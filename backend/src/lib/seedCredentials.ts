export function readOptionalSeedPassword(value: string | undefined) {
	return value?.trim() ? value : undefined;
}
