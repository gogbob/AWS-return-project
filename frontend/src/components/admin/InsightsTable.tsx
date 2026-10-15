import { useEffect, useState } from 'react';
import { api, Insight } from '../../api/client';
import InsightDetail from './InsightDetail';

const CATEGORIES = ['all', 'sneaker', 'boot', 'sandal', 'heel', 'flat', 'loafer', 'handbag', 'wallet', 'sock', 'other'];

export default function InsightsTable() {
  const [insights, setInsights] = useState<Insight[]>([]);
  const [category, setCategory] = useState('all');
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .getInsights(category === 'all' ? undefined : category)
      .then(setInsights)
      .finally(() => setLoading(false));
  }, [category]);

  if (selected) {
    return <InsightDetail itemId={selected} onBack={() => setSelected(null)} />;
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-700">Insights</h2>
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="border border-gray-300 rounded px-3 py-1.5 text-sm bg-white focus:outline-none focus:ring-1 focus:ring-black"
        >
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c === 'all' ? 'All categories' : c}
            </option>
          ))}
        </select>
      </div>

      {loading && <p className="text-sm text-gray-400">Loading…</p>}

      {!loading && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-gray-200 text-left">
                <th className="py-2 pr-4 font-medium text-gray-500 text-xs uppercase">Item</th>
                <th className="py-2 pr-4 font-medium text-gray-500 text-xs uppercase">Category</th>
                <th className="py-2 pr-4 font-medium text-gray-500 text-xs uppercase">n</th>
                <th className="py-2 pr-4 font-medium text-gray-500 text-xs uppercase">n_eff</th>
                <th className="py-2 pr-4 font-medium text-gray-500 text-xs uppercase">JS dist</th>
                <th className="py-2 font-medium text-gray-500 text-xs uppercase">Flags</th>
              </tr>
            </thead>
            <tbody>
              {insights.map((row) => (
                <tr
                  key={row.item_id}
                  onClick={() => setSelected(row.item_id)}
                  className="border-b border-gray-100 hover:bg-gray-50 cursor-pointer"
                >
                  <td className="py-3 pr-4 font-medium max-w-xs truncate">{row.item_name}</td>
                  <td className="py-3 pr-4 text-gray-500 text-xs">{row.category}</td>
                  <td className="py-3 pr-4 text-gray-700 font-mono text-xs">{row.n}</td>
                  <td className="py-3 pr-4 text-gray-700 font-mono text-xs">{row.n_eff.toFixed(1)}</td>
                  <td className="py-3 pr-4 text-gray-700 font-mono text-xs">{row.js_distance.toFixed(3)}</td>
                  <td className="py-3">
                    <div className="flex flex-wrap gap-1">
                      {row.flags.map((f) => (
                        <span
                          key={f}
                          className="px-1.5 py-0.5 bg-yellow-100 text-yellow-800 text-xs rounded"
                        >
                          {f}
                        </span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
              {insights.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-6 text-center text-sm text-gray-400">
                    No insights available.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
