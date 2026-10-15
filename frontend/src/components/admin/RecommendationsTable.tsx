import { useEffect, useState } from 'react';
import { api, Recommendation } from '../../api/client';

export default function RecommendationsTable() {
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      const data = await api.getRecommendations();
      setRecs(data);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function decide(id: string, decision: string) {
    await api.recommendationDecision(id, decision);
    load();
  }

  function typeBadge(type: string) {
    const colors: Record<string, string> = {
      SIZE_GUIDANCE: 'bg-blue-100 text-blue-800',
      DEFECT_FLAG: 'bg-red-100 text-red-800',
      DESCRIPTION_REVIEW: 'bg-purple-100 text-purple-800',
    };
    return (
      <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${colors[type] ?? 'bg-gray-100 text-gray-700'}`}>
        {type.replace(/_/g, ' ')}
      </span>
    );
  }

  function confidenceBadge(c: string) {
    return (
      <span className={`px-1.5 py-0.5 rounded text-xs ${c === 'high' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'}`}>
        {c}
      </span>
    );
  }

  if (loading) return <p className="text-sm text-gray-400">Loading…</p>;

  return (
    <div>
      <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-700 mb-4">
        Recommendations
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-gray-200 text-left">
              <th className="py-2 pr-4 font-medium text-gray-500 text-xs uppercase">Item</th>
              <th className="py-2 pr-4 font-medium text-gray-500 text-xs uppercase">Type</th>
              <th className="py-2 pr-4 font-medium text-gray-500 text-xs uppercase">Reason</th>
              <th className="py-2 pr-4 font-medium text-gray-500 text-xs uppercase">Draft text</th>
              <th className="py-2 pr-4 font-medium text-gray-500 text-xs uppercase">Confidence</th>
              <th className="py-2 pr-4 font-medium text-gray-500 text-xs uppercase">Status</th>
              <th className="py-2 font-medium text-gray-500 text-xs uppercase">Actions</th>
            </tr>
          </thead>
          <tbody>
            {recs.map((r) => (
              <tr key={r.id} className="border-b border-gray-100 align-top">
                <td className="py-3 pr-4 font-medium max-w-[160px] truncate">{r.item_name}</td>
                <td className="py-3 pr-4">{typeBadge(r.type)}</td>
                <td className="py-3 pr-4 text-gray-500 text-xs">{r.reason.replace(/_/g, ' ')}</td>
                <td className="py-3 pr-4 text-gray-600 text-xs max-w-[200px]">{r.draft_text}</td>
                <td className="py-3 pr-4">{confidenceBadge(r.confidence)}</td>
                <td className="py-3 pr-4">
                  <span className="text-xs text-gray-500">{r.status}</span>
                </td>
                <td className="py-3">
                  <div className="flex gap-1 flex-wrap">
                    {r.status === 'proposed' && (
                      <>
                        <button
                          onClick={() => decide(r.id, 'approve')}
                          className="px-2 py-1 bg-black text-white text-xs hover:bg-gray-800"
                        >
                          Approve
                        </button>
                        <button
                          onClick={() => decide(r.id, 'reject')}
                          className="px-2 py-1 border border-gray-300 text-xs hover:bg-gray-50"
                        >
                          Reject
                        </button>
                      </>
                    )}
                    {(r.status === 'approved' || r.status === 'applied') && (
                      <button
                        onClick={() => decide(r.id, 'rollback')}
                        className="px-2 py-1 border border-gray-300 text-xs hover:bg-gray-50"
                      >
                        Rollback
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {recs.length === 0 && (
              <tr>
                <td colSpan={7} className="py-6 text-center text-sm text-gray-400">
                  No recommendations yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
