import { useEffect, useState } from 'react';
import { api, InsightDetail as InsightDetailType } from '../../api/client';

interface InsightDetailProps {
  itemId: string;
  onBack: () => void;
}

export default function InsightDetail({ itemId, onBack }: InsightDetailProps) {
  const [detail, setDetail] = useState<InsightDetailType | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .getInsightDetail(itemId)
      .then(setDetail)
      .finally(() => setLoading(false));
  }, [itemId]);

  if (loading) return <p className="text-sm text-gray-400">Loading…</p>;
  if (!detail) return <p className="text-sm text-red-500">Failed to load detail.</p>;

  const reasons = Object.keys({ ...detail.item_shares, ...detail.baseline_shares });
  const maxVal = Math.max(
    ...Object.values(detail.item_shares),
    ...Object.values(detail.baseline_shares),
    0.01
  );

  return (
    <div className="flex flex-col gap-6">
      <button onClick={onBack} className="text-sm text-gray-500 hover:text-black w-fit">
        ← Back
      </button>

      <div>
        <h2 className="text-base font-semibold text-black">{detail.item_name}</h2>
        <p className="text-xs text-gray-400 mt-0.5">
          Category: {detail.category} · Baseline: {detail.baseline_scope} · n={detail.n} · n_eff=
          {detail.n_eff.toFixed(1)} · JS={detail.js_distance.toFixed(3)}
        </p>
      </div>

      {/* Bar chart */}
      <div>
        <h3 className="text-xs font-medium uppercase tracking-wider text-gray-500 mb-3">
          Return reason distribution
        </h3>
        <div className="flex gap-4 text-xs text-gray-500 mb-2">
          <span className="flex items-center gap-1">
            <span className="w-3 h-3 bg-black inline-block rounded-sm" /> Item
          </span>
          <span className="flex items-center gap-1">
            <span className="w-3 h-3 bg-gray-300 inline-block rounded-sm" /> Baseline
          </span>
        </div>
        <div className="space-y-2">
          {reasons.map((r) => {
            const itemVal = detail.item_shares[r] ?? 0;
            const baseVal = detail.baseline_shares[r] ?? 0;
            const pVal = detail.p_values[r];
            return (
              <div key={r}>
                <div className="flex items-center justify-between text-xs mb-0.5">
                  <span className="text-gray-700">{r.replace(/_/g, ' ')}</span>
                  {pVal != null && (
                    <span className="text-gray-400 font-mono">p={pVal.toFixed(4)}</span>
                  )}
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="flex items-center gap-2">
                    <div
                      className="h-4 bg-black rounded-sm"
                      style={{ width: `${(itemVal / maxVal) * 100}%`, minWidth: itemVal > 0 ? '2px' : '0' }}
                    />
                    <span className="text-xs text-gray-600 font-mono">{(itemVal * 100).toFixed(1)}%</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div
                      className="h-4 bg-gray-300 rounded-sm"
                      style={{ width: `${(baseVal / maxVal) * 100}%`, minWidth: baseVal > 0 ? '2px' : '0' }}
                    />
                    <span className="text-xs text-gray-400 font-mono">{(baseVal * 100).toFixed(1)}%</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
