interface ReturnMethodStepProps {
  onOnline: () => void;
  onInStore: () => void;
}

export default function ReturnMethodStep({ onOnline, onInStore }: ReturnMethodStepProps) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-gray-600">How would you like to return your item?</p>
      <div className="flex gap-3">
        <button
          onClick={onOnline}
          className="flex-1 border border-black py-3 text-sm font-medium uppercase tracking-wider hover:bg-black hover:text-white transition-colors"
        >
          Return online
        </button>
        <button
          onClick={onInStore}
          className="flex-1 border border-black py-3 text-sm font-medium uppercase tracking-wider hover:bg-black hover:text-white transition-colors"
        >
          Return in-store
        </button>
      </div>
    </div>
  );
}
