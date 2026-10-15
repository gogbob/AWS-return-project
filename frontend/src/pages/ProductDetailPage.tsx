import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api, ProductDetail } from '../api/client';
import Header from '../components/Header';
import ReturnModal from '../components/ReturnModal';

function formatTimestamp(value: string | null): string {
  if (!value) return '';
  const parsed = new Date(value);
  if (isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

export default function ProductDetailPage() {
  const { itemId } = useParams<{ itemId: string }>();
  const [product, setProduct] = useState<ProductDetail | null>(null);
  const [error, setError] = useState('');
  const [showModal, setShowModal] = useState(false);

  useEffect(() => {
    if (!itemId) return;
    api
      .getProductDetail(itemId)
      .then((p) => {
        setProduct(p);
        setError('');
      })
      .catch(() => setError('We could not load this product right now.'));
  }, [itemId]);

  return (
    <div className="min-h-screen bg-white text-black font-[system-ui,Inter,sans-serif]">
      <Header onReturnClick={() => setShowModal(true)} />

      <main className="max-w-4xl mx-auto px-6 py-12 flex flex-col md:flex-row gap-12">
        {/* Product image placeholder */}
        <div className="flex-shrink-0">
          <div
            className="bg-gray-100 w-64 h-64 flex items-center justify-center text-gray-300 text-sm"
            aria-label="Product image placeholder"
          >
            200 × 200
          </div>
        </div>

        {/* Product info */}
        <div className="flex flex-col gap-5 flex-1">
          {error && <p className="text-sm text-red-600">{error}</p>}

          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-black uppercase">
              {product?.item_name ?? 'Loading…'}
            </h1>
            {product?.price != null && (
              <p className="text-xl mt-1 text-black font-light">
                ${product.price.toFixed(2)}
              </p>
            )}
          </div>

          {/* AI listing update */}
          {product?.listing_change && (
            <div className="bg-yellow-100 border border-yellow-300 text-yellow-800 px-4 py-3 text-sm rounded">
              <p className="font-medium uppercase tracking-widest text-xs mb-1">
                Updated by ALDO (AI listing update)
              </p>
              <p className="leading-relaxed">{product.listing_change.draft_text}</p>
              {product.listing_change.updated_at && (
                <p className="text-xs text-yellow-700 mt-2">
                  Updated {formatTimestamp(product.listing_change.updated_at)}
                </p>
              )}
            </div>
          )}

          {/* Description */}
          {product && (
            <p className="text-sm text-gray-600 leading-relaxed max-w-sm">
              {product.description}
            </p>
          )}
        </div>
      </main>

      {showModal && <ReturnModal onClose={() => setShowModal(false)} />}
    </div>
  );
}
