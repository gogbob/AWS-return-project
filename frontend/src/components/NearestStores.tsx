import { useEffect, useState } from 'react';
import { api, Store } from '../api/client';

export default function NearestStores() {
  const [stores, setStores] = useState<Store[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [geoError, setGeoError] = useState(false);

  useEffect(() => {
    if (!navigator.geolocation) {
      loadAll();
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        api
          .getNearestStores(pos.coords.latitude, pos.coords.longitude)
          .then((list) => {
            setStores(list);
            setLoading(false);
          })
          .catch(() => loadAll());
      },
      () => {
        setGeoError(true);
        loadAll();
      }
    );
  }, []);

  function loadAll() {
    api
      .getAllStores()
      .then((list) => setStores(list))
      .finally(() => setLoading(false));
  }

  const filtered = searchQuery
    ? stores.filter((s) =>
        s.name.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : stores;

  function mapsUrl(store: Store) {
    const q = encodeURIComponent(`${store.name} ${store.address}`);
    return `https://www.google.com/maps/search/?api=1&query=${q}`;
  }

  if (loading) {
    return <p className="text-sm text-gray-400">Finding stores…</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      {geoError && (
        <p className="text-xs text-gray-500">
          Location unavailable — showing all stores.
        </p>
      )}
      {!geoError && stores[0]?.distance_km != null && (
        <p className="text-xs text-gray-500">3 nearest stores</p>
      )}
      {(geoError || stores[0]?.distance_km == null) && (
        <input
          type="text"
          placeholder="Search stores…"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-black"
        />
      )}
      <ul className="divide-y divide-gray-100 border border-gray-200 rounded">
        {filtered.map((store) => (
          <li key={store.name} className="px-4 py-3">
            <p className="text-sm font-medium text-black">{store.name}</p>
            <p className="text-xs text-gray-500 mt-0.5">{store.address}</p>
            {store.distance_km != null && (
              <p className="text-xs text-gray-400 mt-0.5">
                {store.distance_km.toFixed(1)} km away
              </p>
            )}
            <a
              href={mapsUrl(store)}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-black underline mt-1 inline-block"
            >
              Open in Google Maps
            </a>
          </li>
        ))}
        {filtered.length === 0 && (
          <li className="px-4 py-3 text-sm text-gray-400">No stores found.</li>
        )}
      </ul>
    </div>
  );
}
