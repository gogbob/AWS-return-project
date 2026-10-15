import { useState } from 'react';
import { OrderItem } from '../api/client';
import { useCustomer } from '../context/CustomerContext';
import ItemSearch from './ItemSearch';
import ReturnMethodStep from './ReturnMethodStep';
import EligibilityStep from './EligibilityStep';
import NearestStores from './NearestStores';
import ChatWindow from './ChatWindow';

type Step =
  | 'item'
  | 'method'
  | 'instore'
  | 'online'
  | 'chat'
  | 'final_sale';

interface ReturnModalProps {
  onClose: () => void;
}

const STEP_LABELS = ['Item', 'Method', 'Reason', 'Chat'];

export default function ReturnModal({ onClose }: ReturnModalProps) {
  const { customerId } = useCustomer();
  const [step, setStep] = useState<Step>('item');
  const [selectedItem, setSelectedItem] = useState<OrderItem | null>(null);
  const [caseId, setCaseId] = useState('');
  const [initialMessage, setInitialMessage] = useState('');
  const [initialState, setInitialState] = useState('');
  const [confirmation, setConfirmation] = useState('');

  function stepIndex(): number {
    if (step === 'item') return 0;
    if (step === 'method') return 1;
    if (step === 'online' || step === 'instore' || step === 'final_sale') return 2;
    if (step === 'chat') return 3;
    return 0;
  }

  const currentIndex = stepIndex();

  return (
    /* Overlay */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bg-white w-full max-w-md mx-4 flex flex-col shadow-xl max-h-[90vh]">
        {/* Modal header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <h2 className="text-base font-semibold uppercase tracking-wider text-black">
            Return an item
          </h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-gray-400 hover:text-black text-xl leading-none"
          >
            ✕
          </button>
        </div>

        {/* Step indicator */}
        {step !== 'instore' && step !== 'final_sale' && (
          <div className="flex px-6 py-3 gap-2 border-b border-gray-100">
            {STEP_LABELS.map((label, i) => (
              <div key={label} className="flex items-center gap-1">
                <span
                  className={`w-5 h-5 rounded-full text-xs flex items-center justify-center font-medium ${
                    i < currentIndex
                      ? 'bg-black text-white'
                      : i === currentIndex
                      ? 'bg-gray-800 text-white'
                      : 'bg-gray-200 text-gray-400'
                  }`}
                >
                  {i < currentIndex ? '✓' : i + 1}
                </span>
                <span
                  className={`text-xs ${
                    i <= currentIndex ? 'text-black font-medium' : 'text-gray-400'
                  }`}
                >
                  {label}
                </span>
                {i < STEP_LABELS.length - 1 && (
                  <span className="text-gray-300 text-xs mx-1">›</span>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Confirmation banner */}
        {confirmation && (
          <div
            role="status"
            className="mx-6 mt-3 flex items-start gap-2 rounded border border-green-300 bg-green-50 px-4 py-2 text-sm text-green-800"
          >
            <span aria-hidden="true" className="font-bold">✓</span>
            <span>{confirmation}</span>
          </div>
        )}

        {/* Step content */}
        <div className={`px-6 py-5 overflow-y-auto ${step === 'chat' ? 'flex-1 flex flex-col' : ''}`}>
          {step === 'item' && (
            <ItemSearch
              customerId={customerId}
              onSelect={(item) => {
                setSelectedItem(item);
                setConfirmation(`${item.item_name} selected for return (Order #${item.order_number}).`);
                setStep('method');
              }}
            />
          )}

          {step === 'method' && (
            <ReturnMethodStep
              onOnline={() => setStep('online')}
              onInStore={() => setStep('instore')}
            />
          )}

          {step === 'instore' && (
            <div>
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-semibold uppercase tracking-wider">
                  Return in-store
                </h3>
              </div>
              <NearestStores />
              <button
                onClick={onClose}
                className="mt-4 w-full border border-gray-300 py-2 text-sm hover:bg-gray-50"
              >
                Done
              </button>
            </div>
          )}

          {step === 'online' && selectedItem && (
            <EligibilityStep
              customerId={customerId}
              item={selectedItem}
              onApproved={(id, msg, s) => {
                setCaseId(id);
                setInitialMessage(msg);
                setInitialState(s);
                setConfirmation(`Return request confirmed for ${selectedItem.item_name}.`);
                setStep('chat');
              }}
              onFinalSale={() => setStep('final_sale')}
            />
          )}

          {step === 'final_sale' && (
            <div className="flex flex-col gap-4">
              <p className="text-sm text-gray-700">
                This item is final sale and can't be returned online. Contact customer service.
              </p>
              <button
                onClick={onClose}
                className="bg-black text-white py-2 text-sm font-medium uppercase tracking-wider hover:bg-gray-800"
              >
                Talk to a person
              </button>
              <button
                onClick={onClose}
                className="border border-gray-300 py-2 text-sm hover:bg-gray-50"
              >
                Close
              </button>
            </div>
          )}

          {step === 'chat' && caseId && (
            <div className="flex flex-col flex-1 min-h-[320px]">
              <ChatWindow
                caseId={caseId}
                initialMessage={initialMessage}
                initialState={initialState}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
