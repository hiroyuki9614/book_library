import { describe, expect, test } from 'vitest';
import { bookSeeds, categorySeeds } from './seedData.js';

describe('book seed catalog', () => {
	test('確認済みの書庫データを30冊収録する', () => {
		expect(bookSeeds).toHaveLength(30);
	});

	test('書名とカテゴリ名に重複や未定義カテゴリがない', () => {
		const titles = bookSeeds.map((book) => book.title);
		const categoryNames = new Set(categorySeeds.map((category) => category.name));

		expect(new Set(titles).size).toBe(titles.length);
		for (const book of bookSeeds) {
			expect(categoryNames.has(book.categoryName)).toBe(true);
		}
	});
});
