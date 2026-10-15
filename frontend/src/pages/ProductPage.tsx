import { useEffect, useState } from 'react';
import { api, ProductInfo } from '../api/client';
import Header from '../components/Header';
import ReturnModal from '../components/ReturnModal';
import { useBag } from '../context/BagContext';

const SIZES = ['6', '7', '8', '9', '10', '11'];
const PRICE = 149.95;

export default function ProductPage() {
  const [product, setProduct] = useState<ProductInfo | null>(null);
  const [selectedSize, setSelectedSize] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [addNotice, setAddNotice] = useState('');
  const [sizeError, setSizeError] = useState(false);
  const { addItem } = useBag();

  useEffect(() => {
    api.getProduct().then(setProduct).catch(() => {
      // Fallback if backend not running
      setProduct({ item_name: 'ALDO Demo Shoe' });
    });
  }, []);

  function handleAddToBag() {
    if (!selectedSize) {
      setSizeError(true);
      setAddNotice('');
      return;
    }
    setSizeError(false);
    const name = product?.item_name ?? 'ALDO Demo Shoe';
    addItem({ item_name: name, size: selectedSize, price: PRICE });
    setAddNotice(`Added ${name} (size ${selectedSize}) to your bag.`);
  }

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
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-black uppercase">
              {product?.item_name ?? 'Loading…'}
            </h1>
            <p className="text-xl mt-1 text-black font-light">${PRICE.toFixed(2)}</p>
          </div>

          {/* Size guidance banner */}
          {product?.size_guidance_banner && (
            <div className="bg-yellow-100 border border-yellow-300 text-yellow-800 px-4 py-2 text-sm rounded">
              {product.size_guidance_banner}
            </div>
          )}

          {/* Size selector */}
          <div>
            <p className="text-xs font-medium uppercase tracking-widest text-gray-500 mb-2">
              Select size
            </p>
            <div className="flex gap-2 flex-wrap">
              {SIZES.map((size) => (
                <button
                  key={size}
                  onClick={() => {
                    setSelectedSize(size);
                    setSizeError(false);
                  }}
                  className={`w-12 h-12 border text-sm font-medium transition-colors ${
                    selectedSize === size
                      ? 'border-black bg-black text-white'
                      : 'border-gray-300 text-gray-700 hover:border-black'
                  }`}
                >
                  {size}
                </button>
              ))}
            </div>
            {sizeError && (
              <p className="text-sm text-red-600 mt-2">Please select a size first.</p>
            )}
          </div>

          {/* Description */}
          <p className="text-sm text-gray-600 leading-relaxed max-w-sm">
            Crafted with premium materials and designed for everyday comfort, this style pairs
            effortlessly with any wardrobe. A ALDO signature silhouette with a timeless finish.
          </p>

          {/* Add to bag */}
          <button
            onClick={handleAddToBag}
            className="w-full md:max-w-xs bg-black text-white py-3 text-sm font-medium uppercase tracking-wider hover:bg-gray-800 transition-colors"
          >
            Add to bag
          </button>

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

      {showModal && <ReturnModal onClose={() => setShowModal(false)} />}
    </div>
  );
}
