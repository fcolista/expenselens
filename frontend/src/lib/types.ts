export interface User {
  id: number;
  email: string;
  display_name: string;
  household_id: number;
  mfa_enabled: boolean;
  is_admin: boolean;
}

export interface Category {
  id: number;
  name: string;
  color: string;
  icon?: string;
  monthly_budget?: string | null;
}

export interface Tag {
  id: number;
  name: string;
  category_ids: number[];
}

export interface Expense {
  id: number;
  amount: string;
  currency: string;
  spent_on: string;
  merchant: string;
  description: string;
  category_id: number | null;
  tag_ids: number[];
  payment_method: string | null;
  visibility: 'personal' | 'shared';
  review_status: 'reviewed' | 'needs_review';
  document_key: string | null;
  tax_amount: string | null;
  items: ExpenseLineItem[];
  created_by: number;
}

export interface ExpenseLineItem {
  description: string;
  quantity: string | null;
  unit_price: string | null;
  total: string | null;
}

export interface ExpenseLineItemWrite {
  description: string;
  quantity: number | null;
  unit_price: number | null;
  total: number | null;
}

export type ExpenseWrite = Omit<Expense, 'id' | 'created_by' | 'amount' | 'tax_amount' | 'items'> & {
  amount: number;
  tax_amount: number | null;
  items: ExpenseLineItemWrite[];
};

export interface ReceiptExtraction {
  spent_on: string | null;
  merchant: string | null;
  amount: string | null;
  currency: string;
  tax_amount: string | null;
  suggested_category: string | null;
  description: string;
  payment_method: 'Contanti' | 'Carta' | 'Elettronico' | null;
  items: ExpenseLineItem[];
}

export interface Report {
  start: string;
  end: string;
  currency: string;
  total: string;
  count: number;
  by_category: { name: string; total: string }[];
  by_day: { date: string; total: string }[];
  top_merchants: { name: string; total: string }[];
  by_tag: { name: string; total: string }[];
  by_user: { name: string; total: string }[];
}
