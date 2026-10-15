import { useEffect, useState } from 'react';
import { api, ActivityEntry } from '../../api/client';

export default function ActivityLog() {
  const [entries, setEntries] = useState<ActivityEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [stopping, setStopping] = useState<string | null>(null);

  async function load() {
    try {
      const data = await api.getActivity();
      setEntries(data);
    } finally {
      setLoading(false);
    }
  }

  // Initial load + light polling so new AI decisions appear automatically.
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  async function handleStop(caseId: string) {
    setStopping(caseId);
    try {
      await api.stopCase(caseId);
      await load();
    } finally {
      setStopping(null);
    }
  }

  function statusBadge(status: string | null) {
    const map: Record<string, string> = {
      APPROVED_NO_FEE: 'bg-green-100 text-green-800',
      APPROVED_WITH_FEE: 'bg-blue-100 text-blue-800',
      HUMAN_REVIEW: 'bg-yellow-100 text-yellow-800',
      VISION_REVIEW: 'bg-yellow-100 text-yellow-800',
      REJECTED: 'bg-red-100 text-red-800',
      STOPPED: 'bg-gray-200 text-gray-700',
      PENDING: 'bg-gray-100 text-gray-600',
    };
    const cls = status ? map[status] ?? 'bg-gray-100 text-gray-600' : 'bg-gray-100 text-gray-600';
    return (
      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${cls}`}>
        {status ?? 'UNKNOWN'}
      </span>
    );
  }

  if (loading) return <p className="text-sm text-gray-400">Loading activity…</p>;

  if (entries.length === 0) {
    return (
      <div className="text-sm text-gray-500">
        <p>No AI activity yet.</p>
        <p className="mt-2 text-gray-400">
          Start a return from the storefront (checkout an item, then "Return an item")
          and the AI's shipping-fee decisions will appear here.
        </p>
      </div>
    );
  }

  // Track which case ids we've already shown a Stop button for (newest entry).
  const seenCases = new Set<string>();

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold uppercase tracking-wider">AI activity log</h2>
        <button
          onClick={load}
          className="text-xs border border-gray-300 px-3 py-1 hover:bg-gray-50 uppercase tracking-wider"
        >
          Refresh
        </button>
      </div>

      <ul className="space-y-3">
        {entries.map((e, i) => {
          const canStop =
            e.status !== 'STOPPED' &&
            e.status !== 'APPROVED_NO_FEE' &&
            e.status !== 'APPROVED_WITH_FEE' &&
            e.status !== 'REJECTED' &&
            !seenCases.has(e.case_id);
          seenCases.add(e.case_id);

          return (
            <li
              key={`${e.case_id}-${i}`}
              className="border border-gray-200 rounded px-4 py-3"
            >
              <div className="flex items-center justify-between gap-3 mb-1">
                <div className="flex items-center gap-2 text-xs text-gray-500">
                  <span className="font-medium text-black">{e.customer_label}</span>
                  {e.item_name && <span>· {e.item_name}</span>}
                  <span className="text-gray-300">·</span>
                  <span className="font-mono">{e.case_id.slice(0, 8)}</span>
                </div>
                {statusBadge(e.status)}
              </div>
              <p className="text-sm text-gray-800 leading-relaxed">{e.content}</p>
              {canStop && (
                <div className="mt-2">
                  <button
                    onClick={() => handleStop(e.case_id)}
                    disabled={stopping === e.case_id}
                    className="text-xs bg-red-600 text-white px-3 py-1 uppercase tracking-wider hover:bg-red-700 disabled:opacity-40"
                  >
                    {stopping === e.case_id ? 'Stopping…' : 'Stop AI for this case'}
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
