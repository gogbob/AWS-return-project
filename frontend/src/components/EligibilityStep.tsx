import { useState } from 'react';
import { api, OrderItem } from '../api/client';

const REASONS = [
  { code: 'too_small', label: 'Too small' },
  { code: 'too_big', label: 'Too big' },
  { code: 'too_narrow', label: 'Too narrow' },
  { code: 'too_wide', label: 'Too wide' },
  { code: 'too_thin', label: 'Too thin' },
  { code: 'broken_part', label: 'Broken part' },
  { code: 'damaged', label: 'Damaged' },
  { code: 'not_as_described', label: 'Not as described' },
  { code: 'changed_mind', label: 'Changed my mind' },
  { code: 'other', label: 'Other' },
];

interface EligibilityStepProps {
  customerId: string;
  item: OrderItem;
  onApproved: (caseId: string, initialMessage: string, state: string, reason: string) => void;
  onFinalSale: () => void;
}

export default function EligibilityStep({
  customerId,
  item,
  onApproved,
  onFinalSale,
}: EligibilityStepProps) {
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit() {
    if (!reason) {
      setError('Please select a reason.');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const resp = await api.startReturn({
        customer_id: customerId,
        order_number: item.order_number,
        sku: item.sku,
        item_id: item.item_id,
        reason,
      });
      onApproved(resp.case_id, resp.initial_message, resp.state, reason);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.toLowerCase().includes('final sale')) {
        onFinalSale();
      } else {
        setError(msg || 'Unable to start return. Please try again.');
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="text-sm font-medium text-gray-700 mb-1">
          Returning: <span className="text-black font-semibold">{item.item_name}</span>
        </p>
        <p className="text-xs text-gray-400">Order #{item.order_number}</p>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Reason for return</label>
        <select
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-black bg-white"
        >
          <option value="">Select a reason…</option>
          {REASONS.map((r) => (
            <option key={r.code} value={r.code}>
              {r.label}
            </option>
          ))}
        </select>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <button
        onClick={handleSubmit}
        disabled={loading || !reason}
        className="bg-black text-white py-2 text-sm font-medium uppercase tracking-wider hover:bg-gray-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {loading ? 'Checking…' : 'Start return'}
      </button>
    </div>
  );
}
