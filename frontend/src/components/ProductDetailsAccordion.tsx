import { useState } from 'react';

interface ProductDetailsAccordionProps {
  description?: string;
  materials?: string[];
  features?: string[];
  measurements?: string[];
}

const DEFAULT_MATERIALS = ['Material: Shiny Leather', 'Sole: Rubber'];
const DEFAULT_FEATURES = ['Closed round toe', 'Buckle closure', 'Style Code #14447794'];
const DEFAULT_MEASUREMENTS = ['Heel Height: 0.25"'];

function Section({
  title,
  defaultOpen = false,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t border-gray-200">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between py-4 text-left"
      >
        <span className="text-base font-semibold text-black">{title}</span>
        <span aria-hidden="true" className="text-gray-500 text-sm">
          {open ? '▲' : '▼'}
        </span>
      </button>
      {open && <div className="pb-5">{children}</div>}
    </div>
  );
}

export default function ProductDetailsAccordion({
  description,
  materials = DEFAULT_MATERIALS,
  features = DEFAULT_FEATURES,
  measurements = DEFAULT_MEASUREMENTS,
}: ProductDetailsAccordionProps) {
  return (
    <div className="mt-10 max-w-3xl">
      <Section title="Product Details" defaultOpen>
        {description && (
          <p className="text-sm text-gray-600 leading-relaxed mb-5">{description}</p>
        )}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
          <div>
            <p className="text-xs uppercase tracking-widest text-gray-400 mb-2">Materials</p>
            <ul className="text-sm text-gray-700 space-y-1">
              {materials.map((m) => (
                <li key={m}>· {m}</li>
              ))}
            </ul>
            <p className="text-xs uppercase tracking-widest text-gray-400 mt-4 mb-2">
              Measurements
            </p>
            <ul className="text-sm text-gray-700 space-y-1">
              {measurements.map((m) => (
                <li key={m}>· {m}</li>
              ))}
            </ul>
          </div>
          <div>
            <p className="text-xs uppercase tracking-widest text-gray-400 mb-2">Features</p>
            <ul className="text-sm text-gray-700 space-y-1">
              {features.map((f) => (
                <li key={f}>· {f}</li>
              ))}
            </ul>
          </div>
        </div>
        <p className="text-xs text-gray-500 mt-6">
          Purchase this product recently?{' '}
          <span className="font-semibold underline cursor-default">Share Feedback</span>
        </p>
      </Section>

      <Section title="Shipping &amp; Returns">
        <p className="text-sm text-gray-600 leading-relaxed">
          Free standard shipping on qualifying orders. Returns accepted within 30 days —
          start a return any time from the "Return an item" button in the header.
        </p>
      </Section>
    </div>
  );
}
