import type { SurplusItem } from '../../domain/types.js';

/**
 * Dietary and preference profiles — utility as an incentive. A blind box a
 * diabetic or halal-observant recipient can't eat helps nobody; matching
 * boxes to profiles is what makes the premium framing real.
 *
 * Semantics are exclusion-based and safe-by-default: an item with no
 * dietary tags is only shown to recipients with no exclusions (an untagged
 * box could contain anything).
 */

export interface RecipientProfile {
  userId: string;
  /** Categories the recipient never wants, e.g. 'meat'. */
  excludeCategories: string[];
  /** Tags the recipient must avoid, e.g. 'contains-nuts', 'contains-pork'. */
  avoidTags: string[];
}

export class RecipientPreferencesService {
  private profiles = new Map<string, RecipientProfile>();

  setProfile(profile: RecipientProfile): void {
    this.profiles.set(profile.userId, {
      userId: profile.userId,
      excludeCategories: [...profile.excludeCategories],
      avoidTags: [...profile.avoidTags],
    });
  }

  profileOf(userId: string): RecipientProfile | undefined {
    const p = this.profiles.get(userId);
    return p ? { ...p, excludeCategories: [...p.excludeCategories], avoidTags: [...p.avoidTags] } : undefined;
  }

  matches(userId: string, item: SurplusItem): boolean {
    const profile = this.profiles.get(userId);
    if (!profile) return true;
    if (profile.excludeCategories.includes(item.category)) return false;
    if (profile.avoidTags.length === 0) return true;
    if (!item.dietaryTags || item.dietaryTags.length === 0) return false;
    return !item.dietaryTags.some((tag) => profile.avoidTags.includes(tag));
  }

  filterFeed(userId: string, items: SurplusItem[]): SurplusItem[] {
    return items.filter((item) => this.matches(userId, item));
  }
}
