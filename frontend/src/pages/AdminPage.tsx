import { useState } from 'react';
import ItemInsights from '../components/admin/ItemInsights';
import DataHealth from '../components/admin/DataHealth';
import ActivityLog from '../components/admin/ActivityLog';

type Tab = 'activity' | 'item-insights' | 'health';

const TABS: { key: Tab; label: string }[] = [
  { key: 'activity', label: 'Activity log' },
  { key: 'item-insights', label: 'Item insights' },
  { key: 'health', label: 'Data health' },
];

export default function AdminPage() {
  const [activeTab, setActiveTab] = useState<Tab>('activity');

  return (
    <div className="min-h-screen bg-white text-black font-[system-ui,Inter,sans-serif]">
      {/* Admin header */}
      <header className="flex items-center justify-between px-8 py-4 border-b border-gray-200 bg-white">
        <a href="/" className="text-2xl font-bold tracking-widest text-black uppercase">
          ALDO
        </a>
        <span className="text-sm font-medium text-gray-500 uppercase tracking-wider">
          Admin Console
        </span>
      </header>

      {/* Demo banner */}
      <div className="bg-yellow-100 border-b border-yellow-300 px-8 py-2 text-xs text-yellow-800 font-medium">
        Demo only — no authentication. This console is for demo purposes and is not secured.
      </div>

      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* Tabs */}
        <div className="flex gap-0 border-b border-gray-200 mb-8">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`px-5 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab.key
                  ? 'border-black text-black'
                  : 'border-transparent text-gray-500 hover:text-black'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        {activeTab === 'activity' && <ActivityLog />}
        {activeTab === 'item-insights' && <ItemInsights />}
        {activeTab === 'health' && <DataHealth />}
      </div>
    </div>
  );
}
