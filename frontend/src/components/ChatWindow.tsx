import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';

interface ChatWindowProps {
  caseId: string;
  initialMessage: string;
  initialState: string;
}

type DisplayMessage = { role: 'user' | 'assistant'; content: string };

const FINAL_STATES = ['APPROVED_NO_FEE', 'APPROVED_WITH_FEE', 'REJECTED'];
const POLLING_STATES = ['HUMAN_REVIEW', 'VISION_REVIEW'];

export default function ChatWindow({ caseId, initialMessage, initialState }: ChatWindowProps) {
  const [messages, setMessages] = useState<DisplayMessage[]>([
    { role: 'assistant', content: initialMessage },
  ]);
  const [state, setState] = useState(initialState);
  const [input, setInput] = useState('');
  const [visionRequested, setVisionRequested] = useState(false);
  const [sending, setSending] = useState(false);
  const [photoError, setPhotoError] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Set once a terminal outcome has been shown (by an action handler OR the
  // poll) so we never append a second "approved/rejected" message for the
  // same case — this prevents the phantom duplicate after the final photo.
  const resolvedRef = useRef(false);

  // scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // poll when in review states
  useEffect(() => {
    // Only poll while waiting on an out-of-band (admin) decision, and never
    // once a terminal outcome has already been shown.
    if (POLLING_STATES.includes(state) && !resolvedRef.current) {
      pollRef.current = setInterval(async () => {
        try {
          const s = await api.getCaseState(caseId);
          if (s.state !== state && FINAL_STATES.includes(s.state)) {
            if (pollRef.current) clearInterval(pollRef.current);
            setState(s.state);
            // Guard against a race with an action handler that already
            // displayed the terminal message for this case.
            if (!resolvedRef.current) {
              resolvedRef.current = true;
              const label = stateLabel(s.state, s.fee);
              if (label) {
                setMessages((prev) => [...prev, { role: 'assistant', content: label }]);
              }
            }
          } else if (s.state !== state) {
            setState(s.state);
          }
        } catch {
          // ignore transient errors
        }
      }, 5000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [state, caseId]);

  function stateLabel(s: string, fee?: number): string {
    if (s === 'APPROVED_NO_FEE') return '✓ Return approved — no extra charge.';
    if (s === 'APPROVED_WITH_FEE') return `✓ Return approved — $${fee ?? 7.99} shipping fee.`;
    if (s === 'REJECTED')
      return "Our team couldn't approve this return online. Please return in-store or contact customer service.";
    return '';
  }

  async function handleSend() {
    const text = input.trim();
    if (!text || sending) return;
    setSending(true);
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', content: text }]);
    try {
      const resp = await api.sendMessage(caseId, text);
      setMessages((prev) => [...prev, { role: 'assistant', content: resp.message }]);
      setState(resp.state);
      if (resp.vision_requested) setVisionRequested(true);
      // This turn produced the terminal outcome — stop the poll from
      // re-announcing it.
      if (FINAL_STATES.includes(resp.state)) {
        resolvedRef.current = true;
        if (pollRef.current) clearInterval(pollRef.current);
      }
    } catch (err: unknown) {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content:
            "We couldn't process your input. Please try again, or reach our " +
            'support team at returns@aldo-demo.com or 1-800-555-0123 and ' +
            "we'll help you finish your return.",
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  async function handlePhoto(file: File) {
    if (file.size > 5 * 1024 * 1024) {
      setPhotoError('Photo must be under 5 MB.');
      return;
    }
    setPhotoError('');
    setSending(true);
    setMessages((prev) => [...prev, { role: 'user', content: `[Photo: ${file.name}]` }]);
    try {
      const resp = await api.uploadPhoto(caseId, file);
      setMessages((prev) => [...prev, { role: 'assistant', content: resp.message }]);
      setState(resp.state);
      if (!resp.vision_requested) setVisionRequested(false);
      // The final photo produced the terminal outcome ($7 fee + review); stop
      // the poll so it can't append a second, generic approval message.
      if (FINAL_STATES.includes(resp.state)) {
        resolvedRef.current = true;
        if (pollRef.current) clearInterval(pollRef.current);
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: 'Failed to upload photo. Please try again.' },
      ]);
    } finally {
      setSending(false);
    }
  }

  async function handleRefusePhoto() {
    setSending(true);
    setVisionRequested(false);
    try {
      const resp = await api.refusePhoto(caseId);
      setMessages((prev) => [...prev, { role: 'assistant', content: resp.message }]);
      setState(resp.state);
    } catch {
      // ignore
    } finally {
      setSending(false);
    }
  }

  const isDone = FINAL_STATES.includes(state);
  const isReviewing = POLLING_STATES.includes(state);

  function returnLabelFor(): string {
    return `Return Label: ALDO-RETURN-${caseId.slice(0, 8).toUpperCase()}`;
  }

  return (
    <div className="flex flex-col h-full">
      {/* Message list */}
      <div className="flex-1 overflow-y-auto space-y-3 py-2">
        {messages.map((m, i) => (
          <div
            key={i}
            className={`flex items-start gap-2 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}
          >
            <div
              className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 ${
                m.role === 'user' ? 'bg-black text-white' : 'bg-gray-200 text-gray-700'
              }`}
            >
              {m.role === 'user' ? 'ME' : 'AL'}
            </div>
            <div
              className={`max-w-xs rounded px-3 py-2 text-sm leading-relaxed ${
                m.role === 'user' ? 'bg-black text-white' : 'bg-gray-100 text-gray-800'
              }`}
            >
              {m.content}
            </div>
          </div>
        ))}

        {isReviewing && (
          <p className="text-xs text-gray-500 text-center py-2">
            Your return is being reviewed by our team. This page will update automatically.
          </p>
        )}

        {(state === 'APPROVED_NO_FEE' || state === 'APPROVED_WITH_FEE') && (
          <div className="bg-gray-50 border border-gray-200 rounded p-3 text-xs text-gray-600 font-mono text-center">
            {returnLabelFor()}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Vision upload */}
      {visionRequested && !isDone && (
        <div className="border-t border-gray-200 pt-3 mt-2 flex flex-col gap-2">
          {photoError && <p className="text-xs text-red-600">{photoError}</p>}
          <div className="flex gap-2">
            <button
              onClick={() => fileRef.current?.click()}
              disabled={sending}
              className="flex-1 bg-black text-white py-2 text-xs font-medium uppercase tracking-wider hover:bg-gray-800 disabled:opacity-40"
            >
              Upload photo
            </button>
            <button
              onClick={handleRefusePhoto}
              disabled={sending}
              className="flex-1 border border-gray-300 py-2 text-xs font-medium uppercase tracking-wider hover:bg-gray-50 disabled:opacity-40"
            >
              I'd rather not share a photo
            </button>
          </div>
          <input
            ref={fileRef}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handlePhoto(file);
              e.target.value = '';
            }}
          />
        </div>
      )}

      {/* Text input */}
      {!isDone && !isReviewing && !visionRequested && (
        <div className="border-t border-gray-200 pt-3 mt-2 flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
            placeholder="Type a message…"
            disabled={sending}
            className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-black disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={sending || !input.trim()}
            className="bg-black text-white px-4 py-2 text-sm font-medium hover:bg-gray-800 disabled:opacity-40"
          >
            Send
          </button>
        </div>
      )}
    </div>
  );
}
