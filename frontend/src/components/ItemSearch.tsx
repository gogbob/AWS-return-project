import { useEffect, useRef, useState } from 'react';
import { api, OrderItem } from '../api/client';

interface ItemSearchProps {
  customerId: string;
  onSelect: (item: OrderItem) => void;
}

export default function ItemSearch({ customerId, onSelect }: ItemSearchProps) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<OrderItem[]>([]);
  const [allItems, setAllItems] = useState<OrderItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load all of the customer's returnable items up front (no search needed).
  useEffect(() => {
    if (!customerId) return;
    let cancelled = false;
    setLoading(true);
    api
      .searchItems(customerId, '')
      .then((items) => {
        if (cancelled) return;
        setAllItems(items);
        setResults(items);
        setSearched(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [customerId]);

  // Debounced search; empty query falls back to showing all items.
  useEffect(() => {
    if (!query.trim()) {
      setResults(allItems);
      return;
    }
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const items = await api.searchItems(customerId, query);
        setResults(items);
        setSearched(true);
      } finally {
        setLoading(false);
      }
    }, 300);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [query, customerId, allItems]);

  function handleDropdownSelect(value: string) {
    if (!value) return;
    const item = allItems.find(
      (it) => `${it.order_number}-${it.sku}-${it.item_id}` === value
    );
    if (item) onSelect(item);
  }

  return (
    <div>
      {/* Dropdown of already-ordered items */}
      <label className="block text-sm font-medium text-gray-700 mb-1">
        Pick from your orders
      </label>
      <select
        defaultValue=""
        onChange={(e) => handleDropdownSelect(e.target.value)}
        disabled={loading || allItems.length === 0}
        className="w-full border border-gray-300 rounded px-3 py-2 text-sm bg-white focus:outline-none focus:ring-1 focus:ring-black disabled:opacity-50"
      >
        <option value="">
          {allItems.length > 0
            ? `Select an item (${allItems.length} ordered)…`
            : 'No items found'}
        </option>
        {allItems.map((item) => (
          <option
            key={`${item.order_number}-${item.sku}-${item.item_id}`}
            value={`${item.order_number}-${item.sku}-${item.item_id}`}
          >
            {item.item_name} — Order #{item.order_number}
          </option>
        ))}
      </select>

      {/* Divider */}
      <div className="flex items-center gap-3 my-4">
        <span className="h-px flex-1 bg-gray-200" />
        <span className="text-xs uppercase tracking-widest text-gray-400">or search</span>
        <span className="h-px flex-1 bg-gray-200" />
      </div>

      {/* Search box */}
      <label className="block text-sm font-medium text-gray-700 mb-1">Item name</label>
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search your orders…"
        className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-black"
      />
      {loading && <p className="text-xs text-gray-400 mt-2">Loading…</p>}
      {!loading && searched && results.length === 0 && (
        <p className="text-sm text-gray-500 mt-3">
          We couldn't find that item in your orders.
        </p>
      )}

      {/* Item list — visible without searching */}
      {results.length > 0 && (
        <ul className="mt-3 divide-y divide-gray-100 border border-gray-200 rounded max-h-64 overflow-y-auto">
          {results.map((item) => (
            <li key={`${item.order_number}-${item.sku}-${item.item_id}`}>
              <button
                onClick={() => onSelect(item)}
                className="w-full text-left px-4 py-3 hover:bg-gray-50 transition-colors"
              >
                <p className="text-sm font-medium text-black">{item.item_name}</p>
                <p className="text-xs text-gray-400 mt-0.5">Order #{item.order_number}</p>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
