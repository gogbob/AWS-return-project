import { useEffect, useState } from 'react';
import { api, ItemInsight, ItemInsightReason } from '../../api/client';

const REASON_LABELS: Record<string, string> = {
  too_small: 'Too small',
  too_big: 'Too big',
  too_narrow: 'Too narrow/uncomfortable',
  too_wide: 'Too wide',
  defect: 'Defective / quality',
  not_as_described: 'Not as described',
  changed_mind: 'Changed mind / preference',
  other: 'Other',
};

function reasonLabel(code: string): string {
  return REASON_LABELS[code] ?? code;
}

function formatWhen(ts: string | null): string {
  if (!ts) return '';
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString();
}

export default function ItemInsights() {
  const [items, setItems] = useState<ItemInsight[]>([]);
  const [loading, setLoading] = useState(true);
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    api
      .getItemInsights()
      .then(setItems)
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p className="text-sm text-gray-400">Loading…</p>;

  if (items.length === 0) {
    return (
      <div className="text-sm text-gray-500">
        <p>No item insights available.</p>
        <p className="mt-2 text-gray-400">
          Items appear here once they have resolved returns to analyse.
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-4">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-700">
          Item insights
        </h2>
        <p className="text-xs text-gray-400 mt-1">
          Each item's return reasons vs. its category average. Items flagged red overshoot the
          baseline enough to warrant a change.
        </p>
      </div>

      <ul className="space-y-2">
        {items.map((item) => {
          const open = openId === item.item_id;
          const panelId = `item-insight-panel-${item.item_id}`;
          return (
            <li
              key={item.item_id}
              className={`border rounded ${
                item.overshoot ? 'border-red-300' : 'border-gray-200'
              }`}
            >
              <button
                type="button"
                aria-expanded={open}
                aria-controls={panelId}
                onClick={() => setOpenId(open ? null : item.item_id)}
                className={`w-full text-left px-4 py-3 flex items-center justify-between gap-3 rounded ${
                  item.overshoot
                    ? 'bg-red-50 text-red-800 hover:bg-red-100'
                    : 'bg-white text-black hover:bg-gray-50'
                }`}
              >
                <div className="flex items-center gap-2 min-w-0">
                  {item.overshoot && (
                    <span role="img" aria-label="Return rate anomaly warning" className="shrink-0">
                      ⚠
                    </span>
                  )}
                  <span className="font-medium truncate">{item.item_name}</span>
                  {item.category && (
                    <span className="text-xs text-gray-500 shrink-0">· {item.category}</span>
                  )}
                  <span className="text-xs text-gray-500 shrink-0">· {item.n} returns</span>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  {item.overshoot && (
                    <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-red-100 text-red-800">
                      Return rate anomaly
                    </span>
                  )}
                  {item.needs_input && (
                    <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-yellow-100 text-yellow-800">
                      Needs admin input
                    </span>
                  )}
                  <RecommendationStatus item={item} />
                  <span aria-hidden="true" className="text-gray-400">
                    {open ? '▾' : '▸'}
                  </span>
                </div>
              </button>

              {open && (
                <div id={panelId} className="px-4 py-4 border-t border-gray-200 bg-white">
                  <ReasonBars reasons={item.reasons} />
                  {item.recommendation && (
                    <RecommendationDetail recommendation={item.recommendation} needsInput={item.needs_input} />
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function RecommendationStatus({ item }: { item: ItemInsight }) {
  const rec = item.recommendation;
  if (rec && rec.status === 'applied') {
    return (
      <span className="text-xs text-green-700 font-medium">
        Change applied{rec.updated_at ? ` · ${formatWhen(rec.updated_at)}` : ''}
      </span>
    );
  }
  if (rec && rec.status === 'proposed') {
    return <span className="text-xs text-yellow-800 font-medium">Change proposed · awaiting admin</span>;
  }
  return null;
}

function ReasonBars({ reasons }: { reasons: ItemInsightReason[] }) {
  const sorted = [...reasons].sort((a, b) => b.item_share - a.item_share);
  const maxVal = Math.max(
    0.01,
    ...reasons.map((r) => r.item_share),
    ...reasons.map((r) => r.baseline_share)
  );

  return (
    <div>
      <h3 className="text-xs font-medium uppercase tracking-wider text-gray-500 mb-3">
        Return reason distribution
      </h3>
      <div className="flex gap-4 text-xs text-gray-500 mb-3">
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 bg-black inline-block rounded-sm" /> This item
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 bg-gray-300 inline-block rounded-sm" /> Category average
        </span>
      </div>

      <div className="space-y-3">
        {sorted.map((r) => {
          const overshoot = r.significant && r.excess > 0;
          return (
            <div key={r.reason_code}>
              <div className="flex items-center justify-between text-xs mb-1">
                <span className={overshoot ? 'text-red-700 font-medium' : 'text-gray-700'}>
                  {overshoot && (
                    <span role="img" aria-label="Overshoots category average" className="mr-1">
                      ⚠
                    </span>
                  )}
                  {reasonLabel(r.reason_code)}
                </span>
                {overshoot && (
                  <span className="text-red-700 font-mono">
                    +{(r.excess * 100).toFixed(1)}% over avg
                  </span>
                )}
              </div>
              <div className="flex flex-col gap-0.5">
                {/* Item bar */}
                <div className="flex items-center gap-2">
                  <div
                    className={`h-4 rounded-sm ${overshoot ? 'bg-red-500' : 'bg-black'}`}
                    style={{
                      width: `${(r.item_share / maxVal) * 100}%`,
                      minWidth: r.item_share > 0 ? '2px' : '0',
                    }}
                  />
                  <span className="text-xs text-gray-600 font-mono">
                    {(r.item_share * 100).toFixed(1)}%
                  </span>
                </div>
                {/* Baseline bar (category average) */}
                <div className="flex items-center gap-2">
                  <div
                    className="h-2 bg-gray-300 rounded-sm"
                    style={{
                      width: `${(r.baseline_share / maxVal) * 100}%`,
                      minWidth: r.baseline_share > 0 ? '2px' : '0',
                    }}
                  />
                  <span className="text-xs text-gray-400 font-mono">
                    {(r.baseline_share * 100).toFixed(1)}%
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function RecommendationDetail({
  recommendation,
  needsInput,
}: {
  recommendation: NonNullable<ItemInsight['recommendation']>;
  needsInput: boolean;
}) {
  const isQuality = recommendation.recommendation_type === 'quality_review';
  return (
    <div className="mt-5 pt-4 border-t border-gray-100">
      <h3 className="text-xs font-medium uppercase tracking-wider text-gray-500 mb-2">
        What the AI advises
      </h3>
      <div className="text-sm text-gray-800 space-y-1">
        <p>
          <span className="text-gray-500">Type:</span> {recommendation.recommendation_type ?? '—'}
          {recommendation.reason_code && (
            <span className="text-gray-500"> · reason: {recommendation.reason_code}</span>
          )}
        </p>
        {recommendation.draft_text && (
          <p className="text-gray-700 leading-relaxed">{recommendation.draft_text}</p>
        )}
        {isQuality && (
          <p className="text-red-700">
            Advises a production/quality change — needs admin input to escalate to QA.
          </p>
        )}
        <p className="text-xs text-gray-500">
          Status: {recommendation.status}
          {recommendation.confidence != null && (
            <> · confidence {(recommendation.confidence * 100).toFixed(0)}%</>
          )}
        </p>
        <p className="text-xs text-gray-400">
          {recommendation.created_at && <>Created {formatWhen(recommendation.created_at)}</>}
          {recommendation.updated_at && <> · updated {formatWhen(recommendation.updated_at)}</>}
        </p>
        {needsInput && !isQuality && (
          <p className="text-xs text-yellow-800">Awaiting admin review before this change is applied.</p>
        )}
      </div>
    </div>
  );
}
