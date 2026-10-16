import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api, ProductDetail } from '../api/client';
import Header from '../components/Header';
import ReturnModal from '../components/ReturnModal';
import ProductImage from '../components/ProductImage';
import ProductDetailsAccordion from '../components/ProductDetailsAccordion';
import { useBag } from '../context/BagContext';
import { trueSizeOffset, formatTrueSize, formatTimestamp } from '../lib/truesize';

const SIZES = ['6', '7', '8', '9', '10', '11'];

export default function ProductDetailPage() {
  const { itemId } = useParams<{ itemId: string }>();
  const [product, setProduct] = useState<ProductDetail | null>(null);
  const [error, setError] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [selectedSize, setSelectedSize] = useState('');
  const [sizeError, setSizeError] = useState(false);
  const [addNotice, setAddNotice] = useState('');
  const { addItem } = useBag();

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

  const hasListingChange = !!product?.listing_change;
  const offset = hasListingChange
    ? trueSizeOffset(product?.listing_change?.draft_text)
    : 0;
  const price = product?.price ?? 149.95;

  function handleAddToBag() {
    if (!selectedSize) {
      setSizeError(true);
      setAddNotice('');
      return;
    }
    setSizeError(false);
    const name = product?.item_name ?? 'ALDO Shoe';
    addItem({ item_name: name, size: selectedSize, price });
    setAddNotice(`Added ${name} (size ${selectedSize}) to your bag.`);
  }

  return (
    <div className="min-h-screen bg-white text-black font-[system-ui,Inter,sans-serif]">
      <Header onReturnClick={() => setShowModal(true)} />

      <main className="max-w-4xl mx-auto px-6 py-12 flex flex-col md:flex-row gap-12">
        {/* Product image */}
        <div className="flex-shrink-0">
          <ProductImage
            itemId={product?.item_id ?? itemId}
            alt={product?.item_name ?? 'Product'}
            className="w-80 h-80"
          />
        </div>

        {/* Product info */}
        <div className="flex flex-col gap-5 flex-1">
          {error && <p className="text-sm text-red-600">{error}</p>}

          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-black uppercase">
              {product?.item_name ?? 'Loading…'}
            </h1>
            <p className="text-xl mt-1 text-black font-light">${price.toFixed(2)}</p>
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

          {/* Size selector */}
          {product && (
            <div>
              <p className="text-xs font-medium uppercase tracking-widest text-gray-500 mb-2">
                Select size
                {hasListingChange && (
                  <span className="ml-2 normal-case tracking-normal text-yellow-700">
                    (parentheses show AI-corrected true size)
                  </span>
                )}
              </p>
              <div className="flex gap-2 flex-wrap">
                {SIZES.map((size) => (
                  <button
                    key={size}
                    onClick={() => {
                      setSelectedSize(size);
                      setSizeError(false);
                    }}
                    className={`min-w-12 h-12 px-2 border text-sm font-medium transition-colors ${
                      selectedSize === size
                        ? 'border-black bg-black text-white'
                        : 'border-gray-300 text-gray-700 hover:border-black'
                    }`}
                  >
                    {size}
                    {hasListingChange && (
                      <span
                        className={`ml-1 text-xs ${
                          selectedSize === size ? 'text-gray-300' : 'text-yellow-700'
                        }`}
                      >
                        ({formatTrueSize(size, offset)})
                      </span>
                    )}
                  </button>
                ))}
              </div>
              {hasListingChange && (
                <p className="text-xs text-gray-500 mt-2">
                  True size = the fit this shoe actually runs, based on return data.
                </p>
              )}
              {sizeError && (
                <p className="text-sm text-red-600 mt-2">Please select a size first.</p>
              )}
            </div>
          )}

          {/* Description */}
          {product && (
            <p className="text-sm text-gray-600 leading-relaxed max-w-sm">
              {product.description}
            </p>
          )}

          {/* Add to bag */}
          {product && (
            <button
              onClick={handleAddToBag}
              className="w-full md:max-w-xs bg-black text-white py-3 text-sm font-medium uppercase tracking-wider hover:bg-gray-800 transition-colors"
            >
              Add to bag
            </button>
          )}

          {addNotice && (
            <div
              role="status"
              className="md:max-w-xs flex items-start gap-2 rounded border border-green-300 bg-green-50 px-4 py-2 text-sm text-green-800"
            >
              <span aria-hidden="true" className="font-bold">✓</span>
              <span>{addNotice}</span>
            </div>
          )}
        </div>
      </main>

      {/* Product details dropdown */}
      {product && (
        <div className="max-w-4xl mx-auto px-6 pb-16">
          <ProductDetailsAccordion description={product.description} />
        </div>
      )}

      {showModal && <ReturnModal onClose={() => setShowModal(false)} />}
    </div>
  );
}
