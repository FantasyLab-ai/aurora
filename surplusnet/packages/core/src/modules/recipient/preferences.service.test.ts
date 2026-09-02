import { describe, expect, it } from 'vitest';
import { RecipientPreferencesService } from './preferences.service.js';
import type { SurplusItem } from '../../domain/types.js';

function makeItem(category: string, dietaryTags?: string[]): SurplusItem {
  return {
    id: `item-${category}-${(dietaryTags ?? []).join('.')}`,
    supplierId: 's1',
    title: 'Box',
    category,
    ...(dietaryTags !== undefined ? { dietaryTags } : {}),
    quantity: 1,
    fmvCents: 1000,
    cogsCents: 400,
    calculatedTaxDeductionCents: 700,
    currentState: 'SALES_PHASE',
    listedAt: new Date(),
    salesWindowMinutes: 45,
    safeUntil: new Date(Date.now() + 3_600_000),
    latitude: 40.7,
    longitude: -74.0,
  };
}

describe('RecipientPreferencesService', () => {
  const service = new RecipientPreferencesService();
  service.setProfile({ userId: 'u1', excludeCategories: ['meat'], avoidTags: ['contains-nuts'] });

  it('excludes categories and avoided tags; untagged items are unsafe for avoiders', () => {
    expect(service.matches('u1', makeItem('meat', ['halal']))).toBe(false);
    expect(service.matches('u1', makeItem('bakery', ['contains-nuts']))).toBe(false);
    expect(service.matches('u1', makeItem('bakery', ['vegan']))).toBe(true);
    // No tags at all: could contain anything, so hide it from avoiders
    expect(service.matches('u1', makeItem('bakery'))).toBe(false);
  });

  it('users without a profile see everything', () => {
    expect(service.matches('stranger', makeItem('meat'))).toBe(true);
  });

  it('filters a feed', () => {
    const feed = [makeItem('meat'), makeItem('bakery', ['vegan']), makeItem('produce', ['organic'])];
    expect(service.filterFeed('u1', feed)).toHaveLength(2);
  });
});
