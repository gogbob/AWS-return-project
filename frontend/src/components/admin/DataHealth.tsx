import { useEffect, useState } from 'react';
import { api, DataHealth as DataHealthType, DataSource } from '../../api/client';

export default function DataHealth() {
  const [data, setData] = useState<DataHealthType | null>(null);
  const [source, setSource] = useState<DataSource | null>(null);
  const [loading, setLoading] = useState(true);
  const [recomputing, setRecomputing] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const [d, s] = await Promise.all([api.getDataHealth(), api.getDataSource()]);
      setData(d);
      setSource(s);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleRecompute() {
    setRecomputing(true);
    try {
      await api.recompute();
      await load();
    } finally {
      setRecomputing(false);
    }
  }

  if (loading) return <p className="text-sm text-gray-400">Loading…</p>;
  if (!data) return <p className="text-sm text-red-500">Failed to load data health.</p>;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-700">
          Data health
        </h2>
        <button
          onClick={handleRecompute}
          disabled={recomputing}
          className="bg-black text-white px-4 py-2 text-xs font-medium uppercase tracking-wider hover:bg-gray-800 disabled:opacity-40"
        >
          {recomputing ? 'Recomputing…' : 'Recompute baselines'}
        </button>
      </div>

      {/* Last ingest */}
      <div>
        <p className="text-xs text-gray-500 uppercase font-medium mb-1">Last ingest</p>
        <p className="text-sm">
          {data.last_ingest ? new Date(data.last_ingest).toLocaleString() : 'Never'}
        </p>
      </div>

      {/* Analytics backend */}
      {source && (
        <div>
          <p className="text-xs text-gray-500 uppercase font-medium mb-1">Analytics backend</p>
          <p className="text-sm font-mono">{source.backend_in_use.toUpperCase()}</p>
          <p className="text-xs text-gray-500 mt-1">
            AWS available: <span className="font-mono">{source.aws_available ? 'yes' : 'no'}</span>
          </p>
          <p className="text-xs text-gray-500">
            S3 bucket: <span className="font-mono">{source.s3_bucket}</span>
          </p>
          <p className="text-xs text-gray-500">
            Glue database: <span className="font-mono">{source.glue_database}</span>
          </p>
          <p className="text-xs text-gray-500">
            Last Athena query: <span className="font-mono">{source.last_athena_query_id ?? '—'}</span>
          </p>
        </div>
      )}

      {/* Row counts */}
      <div>
        <p className="text-xs text-gray-500 uppercase font-medium mb-2">Row counts</p>
        <table className="text-sm border-collapse w-full max-w-sm">
          <thead>
            <tr className="border-b border-gray-200 text-left">
              <th className="py-1 pr-6 font-medium text-gray-400 text-xs">Table</th>
              <th className="py-1 font-medium text-gray-400 text-xs">Rows</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(data.row_counts).map(([table, count]) => (
              <tr key={table} className="border-b border-gray-100">
                <td className="py-1.5 pr-6 font-mono text-xs text-gray-700">{table}</td>
                <td className="py-1.5 font-mono text-xs text-gray-700">{count.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Unmapped reasons */}
      <div>
        <p className="text-xs text-gray-500 uppercase font-medium mb-2">Unmapped reasons</p>
        {data.unmapped_reasons.length === 0 ? (
          <p className="text-sm text-green-600">All reasons are mapped.</p>
        ) : (
          <table className="text-sm border-collapse w-full max-w-lg">
            <thead>
              <tr className="border-b border-gray-200 text-left">
                <th className="py-1 pr-6 font-medium text-gray-400 text-xs">Value</th>
                <th className="py-1 pr-6 font-medium text-gray-400 text-xs">Count</th>
                <th className="py-1 font-medium text-gray-400 text-xs">First seen</th>
              </tr>
            </thead>
            <tbody>
              {data.unmapped_reasons.map((row) => (
                <tr key={row.value} className="border-b border-gray-100">
                  <td className="py-1.5 pr-6 font-mono text-xs text-gray-700">{row.value}</td>
                  <td className="py-1.5 pr-6 text-xs text-gray-600">{row.count}</td>
                  <td className="py-1.5 text-xs text-gray-400">{row.first_seen}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
