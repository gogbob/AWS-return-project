const BASE = '/api';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ── Types ─────────────────────────────────────────────────────────────────────

export interface DemoCustomer {
  id: string;
  label: string;
  loyalty_dev?: number; // only populated in dev for tooltip
}

export interface ProductInfo {
  item_name: string;
  size_guidance_banner?: string;
}

export interface OrderItem {
  order_number: string;
  sku: string;
  item_id: string;
  item_name: string;
}

export interface Store {
  name: string;
  address: string;
  lat: number | null;
  lng: number | null;
  verified: boolean;
  distance_km?: number;
}

export interface StartReturnResponse {
  case_id: string;
  initial_message: string;
  state: string;
}

export interface MessageResponse {
  message: string;
  state: string;
  vision_requested?: boolean;
}

export interface CaseState {
  state: string;
  fee?: number;
}

export interface AdminCase {
  id: string;
  customer_label: string;
  item_name: string;
  reason: string;
  status: string;
  created_at: string;
}

export interface TriageComponent {
  component: string;
  weight: number;
  value: number;
  contribution: number;
}

export interface CaseMessage {
  role: string;
  content: string;
  created_at: string;
}

export interface CaseEvidence {
  image_path: string;
  verdict: string;
  confidence: number;
  observations: string;
}

export interface AdminCaseDetail {
  id: string;
  customer_label: string;
  item_name: string;
  reason: string;
  detail?: string;
  loyalty: number;
  status: string;
  triage: TriageComponent[];
  messages: CaseMessage[];
  evidence: CaseEvidence[];
  vision_verdict?: string;
  created_at: string;
}

export interface Insight {
  item_id: string;
  item_name: string;
  category: string;
  n: number;
  n_eff: number;
  js_distance: number;
  flags: string[];
}

export interface InsightDetail extends Insight {
  item_shares: Record<string, number>;
  baseline_shares: Record<string, number>;
  baseline_scope: string;
  p_values: Record<string, number>;
}

export interface Recommendation {
  id: string;
  item_id: string;
  item_name: string;
  type: string;
  reason: string;
  draft_text: string;
  confidence: string;
  status: string;
}

export interface ItemInsightReason {
  reason_code: string;
  item_share: number;
  baseline_share: number;
  excess: number;
  ratio: number;
  significant: boolean;
}

export interface ItemInsightTopFlag {
  reason_code: string;
  group: string;
  excess: number;
  ratio: number;
}

export interface ItemInsightRecommendation {
  id: number;
  recommendation_type: string | null;
  reason_code: string | null;
  draft_text: string | null;
  confidence: number | null;
  status: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface ItemInsight {
  item_id: string;
  item_name: string;
  category: string | null;
  n: number;
  n_eff: number;
  reasons: ItemInsightReason[];
  overshoot: boolean;
  max_excess: number;
  top_flag: ItemInsightTopFlag | null;
  recommendation: ItemInsightRecommendation | null;
  needs_input: boolean;
}

export interface ActivityEntry {
  case_id: string;
  content: string;
  created_at: string;
  customer_label: string;
  item_name: string | null;
  status: string | null;
}

export interface DataHealth {
  last_ingest: string | null;
  row_counts: Record<string, number>;
  unmapped_reasons: Array<{ value: string; count: number; first_seen: string }>;
}

// ── API functions ─────────────────────────────────────────────────────────────

export const api = {
  getDemoCustomers(): Promise<DemoCustomer[]> {
    return request('/demo/customers');
  },

  getProduct(): Promise<ProductInfo> {
    return request('/product');
  },

  searchItems(customer_id: string, q: string): Promise<OrderItem[]> {
    return request(`/items/search?customer_id=${encodeURIComponent(customer_id)}&q=${encodeURIComponent(q)}`);
  },

  getNearestStores(lat: number, lng: number): Promise<Store[]> {
    return request(`/stores/nearest?lat=${lat}&lng=${lng}`);
  },

  getAllStores(): Promise<Store[]> {
    return request('/stores');
  },

  startReturn(payload: {
    customer_id: string;
    order_number: string;
    sku: string;
    item_id: string;
    reason: string;
  }): Promise<StartReturnResponse> {
    return request('/returns/start', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  sendMessage(case_id: string, message: string): Promise<MessageResponse> {
    return request(`/returns/${case_id}/message`, {
      method: 'POST',
      body: JSON.stringify({ message }),
    });
  },

  uploadPhoto(case_id: string, file: File): Promise<MessageResponse> {
    const form = new FormData();
    form.append('file', file);
    return fetch(`${BASE}/returns/${case_id}/photo`, {
      method: 'POST',
      body: form,
    }).then((r) => r.json());
  },

  refusePhoto(case_id: string): Promise<MessageResponse> {
    return request(`/returns/${case_id}/refuse-photo`, { method: 'POST' });
  },

  getCaseState(case_id: string): Promise<CaseState> {
    return request(`/returns/${case_id}`);
  },

  // Admin
  getAdminCases(): Promise<AdminCase[]> {
    return request('/admin/cases');
  },

  getAdminCaseDetail(id: string): Promise<AdminCaseDetail> {
    return request(`/admin/cases/${id}`);
  },

  adminDecision(id: string, decision: string, note?: string): Promise<void> {
    return request(`/admin/cases/${id}/decision`, {
      method: 'POST',
      body: JSON.stringify({ decision, note }),
    });
  },

  getInsights(category?: string): Promise<Insight[]> {
    const q = category ? `?category=${encodeURIComponent(category)}` : '';
    return request(`/admin/insights${q}`);
  },

  getInsightDetail(item_id: string): Promise<InsightDetail> {
    return request(`/admin/insights/${encodeURIComponent(item_id)}`);
  },

  getItemInsights(): Promise<ItemInsight[]> {
    return request('/admin/item-insights');
  },

  getRecommendations(): Promise<Recommendation[]> {
    return request('/admin/recommendations');
  },

  recommendationDecision(id: string, decision: string): Promise<void> {
    return request(`/admin/recommendations/${id}/decision`, {
      method: 'POST',
      body: JSON.stringify({ decision }),
    });
  },

  checkout(
    customer_id: string,
    items: Array<{ item_name: string; size: string; price: number }>
  ): Promise<{ order_number: string; items: OrderItem[] }> {
    return request('/checkout', {
      method: 'POST',
      body: JSON.stringify({ customer_id, items }),
    });
  },

  getActivity(caseId?: string): Promise<ActivityEntry[]> {
    const q = caseId ? `?case_id=${encodeURIComponent(caseId)}` : '';
    return request(`/admin/activity${q}`);
  },

  stopCase(caseId: string): Promise<{ success: boolean; new_status: string }> {
    return request(`/admin/cases/${caseId}/stop`, { method: 'POST' });
  },

  getDataHealth(): Promise<DataHealth> {
    return request('/admin/data-health');
  },

  recompute(): Promise<void> {
    return request('/admin/recompute', { method: 'POST' });
  },
};
