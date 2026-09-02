import { DuplicateTransactionError, NotFoundError, ValidationError } from '../../lib/errors.js';
import type { WalletService } from '../wallet/wallet.service.js';
import type { EngagementService } from './engagement.service.js';

/**
 * Micro-certifications: 10-minute in-app courses (safe food handling,
 * cold-chain basics, hub etiquette) that pay a one-time karma bonus and a
 * permanent badge. Simultaneously an engagement mechanic and the other half
 * of the liability story — dispatch can require a certification for
 * temperature-sensitive categories, and the compliance certificate can show
 * the courier was trained.
 */

export interface CertificationCourse {
  courseId: string;
  title: string;
  karmaBonus: number;
}

export const DEFAULT_COURSES: CertificationCourse[] = [
  { courseId: 'food-handler-101', title: 'Safe Food Handling Basics', karmaBonus: 15 },
  { courseId: 'cold-chain', title: 'Cold-Chain Custody', karmaBonus: 15 },
  { courseId: 'hub-etiquette', title: 'Community Fridge & Hub Etiquette', karmaBonus: 10 },
];

export class CertificationService {
  private courses = new Map<string, CertificationCourse>();
  private completed = new Map<string, Set<string>>();

  constructor(
    private readonly wallets: WalletService,
    private readonly engagement: EngagementService,
    courses: CertificationCourse[] = DEFAULT_COURSES,
  ) {
    for (const course of courses) this.courses.set(course.courseId, { ...course });
  }

  /** One-time per courier per course: badge + karma bonus. */
  async complete(courierId: string, courseId: string): Promise<{ badge: string; karmaBonus: number }> {
    const course = this.courses.get(courseId);
    if (!course) throw new NotFoundError(`no certification course ${courseId}`);

    const done = this.completed.get(courierId) ?? new Set<string>();
    if (done.has(courseId)) {
      throw new ValidationError(`courier ${courierId} already completed ${courseId}`);
    }

    try {
      await this.wallets.credit(
        courierId,
        'KARMA_CREDIT',
        course.karmaBonus,
        `certification:${courseId}`,
        `cert-${courseId}-${courierId}`,
      );
    } catch (err) {
      if (!(err instanceof DuplicateTransactionError)) throw err;
    }

    done.add(courseId);
    this.completed.set(courierId, done);
    const badge = `cert:${courseId}`;
    this.engagement.awardBadge(courierId, badge);
    return { badge, karmaBonus: course.karmaBonus };
  }

  isCertified(courierId: string, courseId: string): boolean {
    return this.completed.get(courierId)?.has(courseId) ?? false;
  }
}
