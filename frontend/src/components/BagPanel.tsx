import { useState } from 'react';
import { useBag } from '../context/BagContext';
import { useCustomer } from '../context/CustomerContext';
import { api } from '../api/client';

interface BagPanelProps {
  onClose: () => void;
}

export default function BagPanel({ onClose }: BagPanelProps) {
  const { items, removeItem, clear } = useBag();
  const { customerId } = useCustomer();
  const [checkingOut, setCheckingOut] = useState(false);
  const [confirmation, setConfirmation] = useState('');
  const [error, setError] = useState('');

  const total = items.reduce((sum, it) => sum + it.price, 0);

  async function handleCheckout() {
    if (items.length === 0 || !customerId) return;
    setCheckingOut(true);
    setError('');
    try {
      const payload = items.map((it) => ({
        item_name: it.item_name,
        size: it.size,
        price: it.price,
      }));
      const resp = await api.checkout(customerId, payload);
      setConfirmation(
        `Order ${resp.order_number} placed. ${resp.items.length} item${
          resp.items.length === 1 ? '' : 's'
        } are now available to return.`
      );
      clear();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg || 'Checkout failed. Please try again.');
    } finally {
      setCheckingOut(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/40"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bg-white w-full max-w-sm h-full flex flex-col shadow-xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <h2 className="text-base font-semibold uppercase tracking-wider text-black">
            Your bag ({items.length})
          </h2>
          <button
            onClick={onClose}
            aria-label="Close bag"
            className="text-gray-400 hover:text-black text-xl leading-none"
          >
            ✕
          </button>
        </div>

        {/* Items */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {confirmation && (
            <div
              role="status"
              className="mb-4 flex items-start gap-2 rounded border border-green-300 bg-green-50 px-4 py-2 text-sm text-green-800"
            >
              <span aria-hidden="true" className="font-bold">✓</span>
              <span>{confirmation}</span>
            </div>
          )}
          {error && (
            <p className="mb-4 text-sm text-red-600">{error}</p>
          )}
          {items.length === 0 ? (
            <p className="text-sm text-gray-500 mt-6 text-center">
              {confirmation ? 'Open "Return an item" to return your purchase.' : 'Your bag is empty.'}
            </p>
          ) : (
            <ul className="divide-y divide-gray-100">
              {items.map((it) => (
                <li key={it.id} className="py-3 flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-black">{it.item_name}</p>
                    <p className="text-xs text-gray-400 mt-0.5">Size {it.size}</p>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-sm text-black">${it.price.toFixed(2)}</span>
                    <button
                      onClick={() => removeItem(it.id)}
                      aria-label={`Remove ${it.item_name}`}
                      className="text-xs text-gray-400 hover:text-red-600 uppercase tracking-wider"
                    >
                      Remove
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Footer */}
        {items.length > 0 && (
          <div className="border-t border-gray-200 px-6 py-4 flex flex-col gap-3">
            <div className="flex items-center justify-between text-sm">
              <span className="font-medium text-gray-700">Total</span>
              <span className="font-semibold text-black">${total.toFixed(2)}</span>
            </div>
            <button
              onClick={handleCheckout}
              disabled={checkingOut}
              className="bg-black text-white py-3 text-sm font-medium uppercase tracking-wider hover:bg-gray-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {checkingOut ? 'Placing order…' : 'Checkout'}
            </button>
            <button
              onClick={clear}
              className="border border-gray-300 py-2 text-sm hover:bg-gray-50"
            >
              Clear bag
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
