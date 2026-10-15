import { useEffect, useState } from 'react';
import { api, AdminCase, AdminCaseDetail } from '../../api/client';
import CaseDetail from './CaseDetail';

export default function CaseQueue() {
  const [cases, setCases] = useState<AdminCase[]>([]);
  const [selected, setSelected] = useState<AdminCaseDetail | null>(null);
  const [loading, setLoading] = useState(true);

  async function loadCases() {
    setLoading(true);
    try {
      const data = await api.getAdminCases();
      setCases(data);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadCases();
  }, []);

  async function openDetail(id: string) {
    const detail = await api.getAdminCaseDetail(id);
    setSelected(detail);
  }

  function statusBadge(status: string) {
    const colors: Record<string, string> = {
      HUMAN_REVIEW: 'bg-yellow-100 text-yellow-800',
      VISION_REVIEW: 'bg-blue-100 text-blue-800',
      APPROVED_NO_FEE: 'bg-green-100 text-green-800',
      APPROVED_WITH_FEE: 'bg-green-100 text-green-800',
      REJECTED: 'bg-red-100 text-red-800',
    };
    const cls = colors[status] ?? 'bg-gray-100 text-gray-700';
    return (
      <span className={`px-2 py-0.5 rounded text-xs font-medium ${cls}`}>
        {status.replace(/_/g, ' ')}
      </span>
    );
  }

  if (loading) return <p className="text-sm text-gray-400">Loading cases…</p>;

  if (selected) {
    return (
      <CaseDetail
        detail={selected}
        onBack={() => setSelected(null)}
        onDecision={async (decision, note) => {
          await api.adminDecision(selected.id, decision, note);
          setSelected(null);
          loadCases();
        }}
      />
    );
  }

  return (
    <div>
      <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-700 mb-4">
        Review queue
      </h2>
      {cases.length === 0 && (
        <p className="text-sm text-gray-400">No cases pending review.</p>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-gray-200 text-left">
              <th className="py-2 pr-4 font-medium text-gray-500 text-xs uppercase">Customer</th>
              <th className="py-2 pr-4 font-medium text-gray-500 text-xs uppercase">Item</th>
              <th className="py-2 pr-4 font-medium text-gray-500 text-xs uppercase">Reason</th>
              <th className="py-2 pr-4 font-medium text-gray-500 text-xs uppercase">Status</th>
              <th className="py-2 pr-4 font-medium text-gray-500 text-xs uppercase">Created</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((c) => (
              <tr
                key={c.id}
                onClick={() => openDetail(c.id)}
                className="border-b border-gray-100 hover:bg-gray-50 cursor-pointer"
              >
                <td className="py-3 pr-4 font-medium">{c.customer_label}</td>
                <td className="py-3 pr-4 text-gray-700">{c.item_name}</td>
                <td className="py-3 pr-4 text-gray-500">{c.reason.replace(/_/g, ' ')}</td>
                <td className="py-3 pr-4">{statusBadge(c.status)}</td>
                <td className="py-3 pr-4 text-gray-400 text-xs">
                  {new Date(c.created_at).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
