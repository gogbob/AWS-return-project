import { useEffect, useState } from 'react';
import { api, DemoCustomer } from '../api/client';
import { useCustomer } from '../context/CustomerContext';

interface HeaderProps {
  onReturnClick: () => void;
}

export default function Header({ onReturnClick }: HeaderProps) {
  const [customers, setCustomers] = useState<DemoCustomer[]>([]);
  const { customerId, setCustomerId } = useCustomer();

  useEffect(() => {
    api.getDemoCustomers().then((list) => {
      setCustomers(list);
      if (!customerId && list.length > 0) {
        setCustomerId(list[0].customer_id);
      }
    });
  }, []);

  return (
    <header className="flex items-center justify-between px-8 py-4 border-b border-gray-200 bg-white">
      {/* ALDO wordmark */}
      <a href="/" className="text-2xl font-bold tracking-widest text-black uppercase select-none">
        ALDO
      </a>

      <div className="flex items-center gap-4">
        {/* Demo customer dropdown */}
        <label className="flex items-center gap-2 text-sm text-gray-600">
          <span className="font-medium">Demo customer:</span>
          <select
            value={customerId}
            onChange={(e) => setCustomerId(e.target.value)}
            className="border border-gray-300 rounded px-2 py-1 text-sm bg-white focus:outline-none focus:ring-1 focus:ring-black"
          >
            {customers.map((c) => (
              <option
                key={c.customer_id}
                value={c.customer_id}
                title={
                  import.meta.env.DEV && c.loyalty != null
                    ? `Loyalty: ${c.loyalty}`
                    : undefined
                }
              >
                {c.label}
              </option>
            ))}
          </select>
        </label>

        {/* Return button */}
        <button
          onClick={onReturnClick}
          disabled={!customerId}
          className="bg-black text-white px-4 py-2 text-sm font-medium uppercase tracking-wider hover:bg-gray-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Return an item
        </button>
      </div>
    </header>
  );
}
