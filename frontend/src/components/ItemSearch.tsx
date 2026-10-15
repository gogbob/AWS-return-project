import { useEffect, useRef, useState } from 'react';
import { api, OrderItem } from '../api/client';

interface ItemSearchProps {
  customerId: string;
  onSelect: (item: OrderItem) => void;
}

export default function ItemSearch({ customerId, onSelect }: ItemSearchProps) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<OrderItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      setSearched(false);
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
  }, [query, customerId]);

  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">Item name</label>
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search your orders…"
        className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-black"
        autoFocus
      />
      {loading && (
        <p className="text-xs text-gray-400 mt-2">Searching…</p>
      )}
      {!loading && searched && results.length === 0 && (
        <p className="text-sm text-gray-500 mt-3">
          We couldn't find that item in your orders.
        </p>
      )}
      {results.length > 0 && (
        <ul className="mt-3 divide-y divide-gray-100 border border-gray-200 rounded">
          {results.map((item) => (
            <li key={`${item.order_number}-${item.sku}`}>
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
