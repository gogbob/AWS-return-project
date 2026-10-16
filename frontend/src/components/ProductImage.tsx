import { useEffect, useState } from 'react';

interface ProductImageProps {
  itemId?: string | null;
  alt: string;
  className?: string;
}

/**
 * Shows the product photo served from /public/products/<item_id>.<ext>.
 * Tries .png, then .jpg, then .jpeg, then .webp. If none load (or no itemId),
 * falls back to the grey "no image" placeholder so the page never breaks.
 */
const EXTS = ['png', 'jpg', 'jpeg', 'webp'];

export default function ProductImage({ itemId, alt, className }: ProductImageProps) {
  const [extIdx, setExtIdx] = useState(0);
  const [failed, setFailed] = useState(false);

  // Reset when the item changes.
  useEffect(() => {
    setExtIdx(0);
    setFailed(false);
  }, [itemId]);

  const box = className ?? 'w-80 h-80';

  if (!itemId || failed) {
    return (
      <div
        className={`bg-gray-100 ${box} flex items-center justify-center text-gray-300 text-sm`}
        aria-label={`${alt} (image unavailable)`}
      >
        No image
      </div>
    );
  }

  const src = `/products/${encodeURIComponent(itemId)}.${EXTS[extIdx]}`;

  return (
    <img
      src={src}
      alt={alt}
      className={`${box} object-contain bg-gray-100`}
      onError={() => {
        if (extIdx < EXTS.length - 1) {
          setExtIdx((i) => i + 1);
        } else {
          setFailed(true);
        }
      }}
    />
  );
}
