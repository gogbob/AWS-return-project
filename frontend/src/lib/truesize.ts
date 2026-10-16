/**
 * Shared "true size" helpers used by both the storefront product page and the
 * product-detail page so the AI size correction renders identically on both.
 *
 * When the AI has updated the listing, we derive a half-size offset from the
 * guidance text:
 *   - "runs large" / "size down" → true size is 0.5 smaller than the label
 *   - "runs small" / "size up"   → true size is 0.5 larger than the label
 * Defaults to -0.5 when the direction isn't explicit.
 */
export function trueSizeOffset(listingText: string | undefined | null): number {
  if (!listingText) return 0;
  const t = listingText.toLowerCase();
  const runsSmall = /runs small|size up|order (a )?(half )?size up|too small/.test(t);
  const runsLarge = /runs large|size down|order (a )?(half )?size down|too (big|large)/.test(t);
  if (runsSmall) return 0.5;
  if (runsLarge) return -0.5;
  return -0.5;
}

export function formatTrueSize(label: string, offset: number): string {
  const n = parseFloat(label) + offset;
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

export function formatTimestamp(value: string | null): string {
  if (!value) return '';
  const parsed = new Date(value);
  return isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}
