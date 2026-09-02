import type { DonationLedger, LedgerEntry } from './donation-ledger.js';

/**
 * Compiles the ledger into the end-of-month, audit-ready summary a supplier
 * hands to their accountant. This service produces the structured data;
 * rendering to PDF is a thin presentation layer on top (kept out of core so
 * the numbers stay independently testable).
 */

export interface MonthlyAuditReport {
  year: number;
  /** 1-12 */
  month: number;
  donationCount: number;
  totalFmvCents: number;
  totalCogsCents: number;
  totalDeductionCents: number;
  /** Non-empty when the hash chain fails verification — the report is then unusable for audit. */
  brokenSequences: number[];
  entries: Array<{
    sequence: number;
    surplusItemId: string;
    recordedAt: Date;
    fmvCents: number;
    cogsCents: number;
    deductionCents: number;
  }>;
}

interface DeductionPayload {
  fmvCents: number;
  cogsCents: number;
  deductionCents: number;
}

function isDeductionPayload(payload: Record<string, unknown>): payload is Record<string, unknown> & DeductionPayload {
  return (
    typeof payload['fmvCents'] === 'number' &&
    typeof payload['cogsCents'] === 'number' &&
    typeof payload['deductionCents'] === 'number'
  );
}

export class AuditExportService {
  constructor(
    private readonly ledger: DonationLedger,
    private readonly listEntries: () => Promise<LedgerEntry[]>,
  ) {}

  async buildMonthlyReport(year: number, month: number): Promise<MonthlyAuditReport> {
    const brokenSequences = await this.ledger.verifyChain();
    const all = await this.listEntries();

    const inMonth = all.filter(
      (e) =>
        e.kind === 'TAX_DEDUCTION_CALCULATED' &&
        e.recordedAt.getUTCFullYear() === year &&
        e.recordedAt.getUTCMonth() + 1 === month &&
        isDeductionPayload(e.payload),
    );

    const entries = inMonth.map((e) => {
      const p = e.payload as unknown as DeductionPayload;
      return {
        sequence: e.sequence,
        surplusItemId: e.surplusItemId,
        recordedAt: e.recordedAt,
        fmvCents: p.fmvCents,
        cogsCents: p.cogsCents,
        deductionCents: p.deductionCents,
      };
    });

    return {
      year,
      month,
      donationCount: entries.length,
      totalFmvCents: entries.reduce((s, e) => s + e.fmvCents, 0),
      totalCogsCents: entries.reduce((s, e) => s + e.cogsCents, 0),
      totalDeductionCents: entries.reduce((s, e) => s + e.deductionCents, 0),
      brokenSequences,
      entries,
    };
  }
}
