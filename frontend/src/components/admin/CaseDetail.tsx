import { useState } from 'react';
import { AdminCaseDetail } from '../../api/client';

interface CaseDetailProps {
  detail: AdminCaseDetail;
  onBack: () => void;
  onDecision: (decision: string, note?: string) => Promise<void>;
}

export default function CaseDetail({ detail, onBack, onDecision }: CaseDetailProps) {
  const [rejectNote, setRejectNote] = useState('');
  const [showRejectInput, setShowRejectInput] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function act(decision: string, note?: string) {
    setSubmitting(true);
    try {
      await onDecision(decision, note);
    } finally {
      setSubmitting(false);
    }
  }

  function verdictBadge(v?: string) {
    if (!v) return null;
    const colors: Record<string, string> = {
      consistent: 'bg-green-100 text-green-800',
      inconsistent: 'bg-red-100 text-red-800',
      unclear: 'bg-gray-100 text-gray-700',
    };
    return (
      <span className={`px-2 py-0.5 rounded text-xs font-medium ${colors[v] ?? 'bg-gray-100'}`}>
        {v}
      </span>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <button
        onClick={onBack}
        className="text-sm text-gray-500 hover:text-black flex items-center gap-1 w-fit"
      >
        ← Back to queue
      </button>

      {/* Header info */}
      <div className="grid grid-cols-2 gap-4 text-sm">
        <div>
          <p className="text-xs text-gray-400 uppercase font-medium">Customer</p>
          <p className="font-semibold mt-0.5">{detail.customer_label}</p>
        </div>
        <div>
          <p className="text-xs text-gray-400 uppercase font-medium">Item</p>
          <p className="mt-0.5">{detail.item_name}</p>
        </div>
        <div>
          <p className="text-xs text-gray-400 uppercase font-medium">Reason</p>
          <p className="mt-0.5">{detail.reason.replace(/_/g, ' ')}</p>
        </div>
        <div>
          <p className="text-xs text-gray-400 uppercase font-medium">Loyalty</p>
          <p className="mt-0.5 font-mono">{detail.loyalty}</p>
        </div>
        {detail.detail && (
          <div className="col-span-2">
            <p className="text-xs text-gray-400 uppercase font-medium">Detail note</p>
            <p className="mt-0.5 text-gray-700">{detail.detail}</p>
          </div>
        )}
      </div>

      {/* Triage breakdown */}
      {detail.triage && detail.triage.length > 0 && (
        <div>
          <h3 className="text-xs font-medium uppercase tracking-wider text-gray-500 mb-2">
            Triage breakdown
          </h3>
          <table className="w-full text-xs border-collapse">
            <thead>
              <tr className="border-b border-gray-200 text-left text-gray-400">
                <th className="py-1 pr-3">Component</th>
                <th className="py-1 pr-3">Weight</th>
                <th className="py-1 pr-3">Value</th>
                <th className="py-1">Contribution</th>
              </tr>
            </thead>
            <tbody>
              {detail.triage.map((row) => (
                <tr key={row.component} className="border-b border-gray-100">
                  <td className="py-1 pr-3 font-medium">{row.component}</td>
                  <td className="py-1 pr-3 text-gray-500">{row.weight}</td>
                  <td className="py-1 pr-3 text-gray-700">{row.value.toFixed(3)}</td>
                  <td className="py-1 text-gray-700">{row.contribution.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Chat transcript */}
      {detail.messages && detail.messages.length > 0 && (
        <div>
          <h3 className="text-xs font-medium uppercase tracking-wider text-gray-500 mb-2">
            Chat transcript
          </h3>
          <div className="space-y-2 max-h-48 overflow-y-auto border border-gray-100 rounded p-3">
            {detail.messages.map((m, i) => (
              <div
                key={i}
                className={`flex items-start gap-2 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}
              >
                <span
                  className={`text-xs font-bold w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 ${
                    m.role === 'user' ? 'bg-black text-white' : 'bg-gray-200 text-gray-600'
                  }`}
                >
                  {m.role === 'user' ? 'C' : 'A'}
                </span>
                <p
                  className={`text-xs rounded px-2 py-1 max-w-xs ${
                    m.role === 'user' ? 'bg-black text-white' : 'bg-gray-100 text-gray-800'
                  }`}
                >
                  {m.content}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Evidence images */}
      {detail.evidence && detail.evidence.length > 0 && (
        <div>
          <h3 className="text-xs font-medium uppercase tracking-wider text-gray-500 mb-2">
            Evidence images
          </h3>
          <div className="flex flex-wrap gap-3">
            {detail.evidence.map((ev, i) => (
              <div key={i} className="flex flex-col gap-1">
                <img
                  src={`/uploads/${ev.image_path}`}
                  alt={`Evidence ${i + 1}`}
                  className="w-28 h-28 object-cover border border-gray-200 rounded"
                />
                <div className="flex items-center gap-1 text-xs">
                  {verdictBadge(ev.verdict)}
                  <span className="text-gray-400">{(ev.confidence * 100).toFixed(0)}%</span>
                </div>
                {ev.observations && (
                  <p className="text-xs text-gray-500 max-w-28">{ev.observations}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Vision verdict summary */}
      {detail.vision_verdict && (
        <div className="flex items-center gap-2 text-sm">
          <span className="text-gray-500">Vision verdict:</span>
          {verdictBadge(detail.vision_verdict)}
        </div>
      )}

      {/* Action buttons */}
      <div className="border-t border-gray-200 pt-4">
        <h3 className="text-xs font-medium uppercase tracking-wider text-gray-500 mb-3">
          Actions
        </h3>
        {showRejectInput ? (
          <div className="flex flex-col gap-2">
            <textarea
              value={rejectNote}
              onChange={(e) => setRejectNote(e.target.value)}
              placeholder="Rejection note (required)…"
              rows={3}
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-black"
            />
            <div className="flex gap-2">
              <button
                onClick={() => act('reject', rejectNote)}
                disabled={!rejectNote.trim() || submitting}
                className="flex-1 bg-red-600 text-white py-2 text-xs font-medium uppercase hover:bg-red-700 disabled:opacity-40"
              >
                Confirm reject
              </button>
              <button
                onClick={() => setShowRejectInput(false)}
                className="flex-1 border border-gray-300 py-2 text-xs hover:bg-gray-50"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => act('approve_no_fee')}
              disabled={submitting}
              className="bg-black text-white px-4 py-2 text-xs font-medium uppercase hover:bg-gray-800 disabled:opacity-40"
            >
              Approve, no fee
            </button>
            <button
              onClick={() => act('approve_with_fee')}
              disabled={submitting}
              className="bg-gray-700 text-white px-4 py-2 text-xs font-medium uppercase hover:bg-gray-800 disabled:opacity-40"
            >
              Approve with fee
            </button>
            <button
              onClick={() => setShowRejectInput(true)}
              disabled={submitting}
              className="border border-red-500 text-red-600 px-4 py-2 text-xs font-medium uppercase hover:bg-red-50 disabled:opacity-40"
            >
              Reject
            </button>
            <button
              onClick={() => act('fraud')}
              disabled={submitting}
              className="border border-gray-400 text-gray-600 px-4 py-2 text-xs font-medium uppercase hover:bg-gray-50 disabled:opacity-40"
            >
              Mark as fraud
            </button>
            <button
              onClick={() => act('release')}
              disabled={submitting}
              className="border border-gray-400 text-gray-600 px-4 py-2 text-xs font-medium uppercase hover:bg-gray-50 disabled:opacity-40"
            >
              Release to evidence
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
